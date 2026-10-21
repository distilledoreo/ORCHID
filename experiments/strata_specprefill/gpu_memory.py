"""Lightweight NVML sampling, separate from torch's student-only allocator peak."""
import argparse
import ctypes
import json
import os
import threading
from pathlib import Path


class Memory(ctypes.Structure):
    _fields_=[('total',ctypes.c_ulonglong),('free',ctypes.c_ulonglong),('used',ctypes.c_ulonglong)]


class MemorySampler:
    def __init__(self):
        self.lib=ctypes.CDLL('libnvidia-ml.so.1')
        if self.lib.nvmlInit_v2()!=0:
            raise RuntimeError('NVML initialization failed')
        self.handle=ctypes.c_void_p()
        if self.lib.nvmlDeviceGetHandleByIndex_v2(0,ctypes.byref(self.handle))!=0:
            raise RuntimeError('NVML GPU lookup failed')
        self.lib.nvmlDeviceGetMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.POINTER(Memory)]
        self.stop_event=threading.Event()
        self.samples=[]

    def sample(self):
        m=Memory()
        if self.lib.nvmlDeviceGetMemoryInfo(self.handle,ctypes.byref(m))!=0:
            raise RuntimeError('NVML memory query failed')
        self.samples.append(m.used/2**20)

    def start(self):
        self.samples=[]
        self.stop_event.clear()
        self.sample()
        def worker():
            while not self.stop_event.wait(.05):
                self.sample()
        self.thread=threading.Thread(target=worker,daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join()
        self.sample()
        return {'sampled_peak_mib':max(self.samples),'sampled_min_mib':min(self.samples),
                'samples':len(self.samples),'interval_s':.05}


def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument('--pid',type=int,required=True)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    sampler=MemorySampler()
    sampler.start()
    while True:
        try:
            os.kill(a.pid,0)
        except ProcessLookupError:
            break
        sampler.stop_event.wait(.2)
    a.output.write_text(json.dumps(sampler.stop(),indent=2)+'\n')


if __name__=='__main__':
    main()
