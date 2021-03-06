# Copyright 2014-2015 Robert Jordens <jordens@gmail.com>
#
# This file is part of redpid.
#
# redpid is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# redpid is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with redpid.  If not, see <http://www.gnu.org/licenses/>.

from migen import *
from misoc.interconnect import csr_bus
from misoc.interconnect.csr import AutoCSR, CSRStatus

# https://github.com/RedPitaya/RedPitaya/blob/master/FPGA/release1/fpga/code/rtl/red_pitaya_daisy.v

from .pitaya_ps import SysCDC, Sys2CSR, SysInterconnect, PitayaPS, sys_layout
from .crg import CRG
from .analog import PitayaAnalog
from .chains import FastChain, SlowChain, cross_connect
from .gpio import Gpio
from .xadc import XADC
from .delta_sigma import DeltaSigma
from .dna import DNA
from .lfsr import XORSHIFTGen


class ScopeGen(Module, AutoCSR):
    def __init__(self, width=25):
        self.trigger = Signal()
        self.scope_sys = Record(sys_layout)
        self.asg_sys = Record(sys_layout)

        adc_a = Signal((width, True))
        adc_b = Signal((width, True))
        dac_a = Signal((width, True))
        dac_b = Signal((width, True))

        self.signal_in = adc_a, adc_b
        self.signal_out = dac_a, dac_b
        self.state_in = ()
        self.state_out = ()

        asg_a = Signal((14, True))
        asg_b = Signal((14, True))
        asg_trig = Signal()

        s = width - len(asg_a)
        self.comb += dac_a.eq(asg_a << s), dac_b.eq(asg_b << s)

        self.specials.scope = Instance("red_pitaya_scope",
                i_adc_a_i=adc_a >> s,
                i_adc_b_i=adc_b >> s,
                i_adc_clk_i=ClockSignal(),
                i_adc_rstn_i=~ResetSignal(),
                i_trig_ext_i=self.trigger,
                i_trig_asg_i=asg_trig,

                i_sys_clk_i=self.scope_sys.clk,
                i_sys_rstn_i=self.scope_sys.rstn,
                i_sys_addr_i=self.scope_sys.addr,
                i_sys_wdata_i=self.scope_sys.wdata,
                i_sys_sel_i=self.scope_sys.sel,
                i_sys_wen_i=self.scope_sys.wen,
                i_sys_ren_i=self.scope_sys.ren,
                o_sys_rdata_o=self.scope_sys.rdata,
                o_sys_err_o=self.scope_sys.err,
                o_sys_ack_o=self.scope_sys.ack,
        )

        self.specials.asg = Instance("red_pitaya_asg",
                o_dac_a_o=asg_a,
                o_dac_b_o=asg_b,
                i_dac_clk_i=ClockSignal(),
                i_dac_rstn_i=~ResetSignal(),
                i_trig_a_i=self.trigger,
                i_trig_b_i=self.trigger,
                o_trig_out_o=asg_trig,

                i_sys_clk_i=self.asg_sys.clk,
                i_sys_rstn_i=self.asg_sys.rstn,
                i_sys_addr_i=self.asg_sys.addr,
                i_sys_wdata_i=self.asg_sys.wdata,
                i_sys_sel_i=self.asg_sys.sel,
                i_sys_wen_i=self.asg_sys.wen,
                i_sys_ren_i=self.asg_sys.ren,
                o_sys_rdata_o=self.asg_sys.rdata,
                o_sys_err_o=self.asg_sys.err,
                o_sys_ack_o=self.asg_sys.ack,
        )


class Pid(Module):
    def __init__(self, platform):
        csr_map = {
                "dna": 28, "xadc": 29, "gpio_n": 30, "gpio_p": 31,
                "fast_a": 0, "fast_b": 1,
                "slow_a": 2, "slow_b": 3, "slow_c": 4, "slow_d": 5,
                "scopegen": 6, "noise": 7,
        }

        self.submodules.analog = PitayaAnalog(
                platform.request("adc"), platform.request("dac"))

        self.submodules.xadc = XADC(platform.request("xadc"))

        sys_double = ClockDomainsRenamer("sys_double")

        for i in range(4):
            pwm = platform.request("pwm", i)
            ds = sys_double(DeltaSigma(width=15))
            self.comb += pwm.eq(ds.out)
            setattr(self.submodules, "ds%i" % i, ds)

        exp = platform.request("exp")
        self.submodules.gpio_n = Gpio(exp.n)
        self.submodules.gpio_p = Gpio(exp.p)

        leds = Cat(*(platform.request("user_led", i) for i in range(8)))
        self.comb += leds.eq(self.gpio_n.o)

        self.submodules.dna = DNA(version=2)

        s, c = 25, 18
        self.submodules.fast_a = FastChain(14, s, c)
        self.submodules.fast_b = FastChain(14, s, c)
        sys_slow = ClockDomainsRenamer("sys_slow")
        self.submodules.slow_a = sys_slow(SlowChain(16, s, c))
        self.slow_a.iir.interval.value.value *= 15
        self.submodules.slow_b = sys_slow(SlowChain(16, s, c))
        self.slow_b.iir.interval.value.value *= 15
        self.submodules.slow_c = sys_slow(SlowChain(16, s, c))
        self.slow_c.iir.interval.value.value *= 15
        self.submodules.slow_d = sys_slow(SlowChain(16, s, c))
        self.slow_d.iir.interval.value.value *= 15
        self.submodules.scopegen = ScopeGen(s)
        #self.submodules.noise = LFSRGen(s)
        self.submodules.noise = XORSHIFTGen(s)

        self.state_names, self.signal_names = cross_connect(self.gpio_n, [
            ("fast_a", self.fast_a), ("fast_b", self.fast_b),
            ("slow_a", self.slow_a), ("slow_b", self.slow_b),
            ("slow_c", self.slow_c), ("slow_d", self.slow_d),
            ("scopegen", self.scopegen), ("noise", self.noise),
        ])

        self.comb += [
                self.scopegen.trigger.eq(self.gpio_p.i[0]),

                self.fast_a.adc.eq(self.analog.adc_a),
                self.fast_b.adc.eq(self.analog.adc_b),
                self.analog.dac_a.eq(self.fast_a.dac),
                self.analog.dac_b.eq(self.fast_b.dac),
                self.slow_a.adc.eq(self.xadc.adc[0] << 4),
                self.ds0.data.eq(self.slow_a.dac),
                self.slow_b.adc.eq(self.xadc.adc[1] << 4),
                self.ds1.data.eq(self.slow_b.dac),
                self.slow_c.adc.eq(self.xadc.adc[2] << 4),
                self.ds2.data.eq(self.slow_c.dac),
                self.slow_d.adc.eq(self.xadc.adc[3] << 4),
                self.ds3.data.eq(self.slow_d.dac),
        ]

        self.submodules.csrbanks = csr_bus.CSRBankArray(self,
                    lambda name, mem: csr_map[name if mem is None
                        else name + "_" + mem.name_override])
        self.submodules.sys2csr = Sys2CSR()
        self.submodules.csrcon = csr_bus.Interconnect(self.sys2csr.csr,
                self.csrbanks.get_buses())
        self.submodules.syscdc = SysCDC()
        self.comb += self.syscdc.target.connect(self.sys2csr.sys)


class DummyID(Module, AutoCSR):
    def __init__(self):
        self.id = CSRStatus(1, reset=1)


class DummyHK(Module, AutoCSR):
    def __init__(self):
        self.submodules.id = DummyID()
        self.submodules.csrbanks = csr_bus.CSRBankArray(self,
                    lambda name, mem: 0)
        self.submodules.sys2csr = Sys2CSR()
        self.submodules.csrcon = csr_bus.Interconnect(self.sys2csr.csr,
                self.csrbanks.get_buses())
        self.sys = self.sys2csr.sys


class RedPid(Module):
    def __init__(self, platform):
        self.submodules.ps = PitayaPS(platform.request("cpu"))
        self.submodules.crg = CRG(platform.request("clk125"),
                self.ps.fclk[0], ~self.ps.frstn[0])
        self.submodules.pid = Pid(platform)

        self.submodules.hk = ClockDomainsRenamer("sys_ps")(DummyHK())

        self.submodules.ic = SysInterconnect(self.ps.axi.sys,
                self.hk.sys, self.pid.scopegen.scope_sys,
                self.pid.scopegen.asg_sys,
                self.pid.syscdc.source)
