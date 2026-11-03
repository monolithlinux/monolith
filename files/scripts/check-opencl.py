#!/usr/bin/python3
"""Stop the build when OpenCL can't run a kernel.

Rusticl, Mesa's OpenCL, loads libclc from a path set when Mesa was built, and
no package dependency makes sure that path exists. On Fedora 45 it didn't, and
OpenCL found no device at all. This compiles and runs a small kernel on
llvmpipe, Mesa's CPU driver, so it works without a GPU. The recipes run it
after their last package change.
"""

import ctypes
from ctypes import byref, c_float, c_int32, c_size_t, c_uint32, c_uint64, c_void_p
import os
import sys

# llvmpipe is off unless asked for, and a shader cache would end up in the image.
os.environ["RUSTICL_ENABLE"] = "llvmpipe"
os.environ["MESA_SHADER_CACHE_DISABLE"] = "true"

PLATFORM_NAME = 0x0902
DEVICE_TYPE_ALL = 0xFFFFFFFF
PROGRAM_BUILD_LOG = 0x1183
MEM_READ_WRITE = 1 << 0
MEM_COPY_HOST_PTR = 1 << 5

# sqrt comes from libclc, so this fails without it.
SOURCE = b"""
__kernel void root_plus_one(__global float *numbers) {
    size_t i = get_global_id(0);
    numbers[i] = sqrt(numbers[i]) + 1.0f;
}
"""
NUMBERS = (4.0, 9.0, 16.0, 25.0)
EXPECTED = [3.0, 4.0, 5.0, 6.0]


class Failed(Exception):
    pass


def check(step, code):
    if code:
        raise Failed(f"{step} failed with OpenCL error {code}.")


def run_kernel():
    """NUMBERS after the kernel ran on rusticl's llvmpipe device."""
    cl = ctypes.CDLL("libOpenCL.so.1")
    for name in ("clCreateContext", "clCreateProgramWithSource", "clCreateBuffer",
                 "clCreateKernel", "clCreateCommandQueueWithProperties"):
        getattr(cl, name).restype = c_void_p

    count = c_uint32()
    cl.clGetPlatformIDs(0, None, byref(count))
    platforms = (c_void_p * count.value)()
    cl.clGetPlatformIDs(count.value, platforms, None)
    rusticl = None
    for platform in platforms:
        name = ctypes.create_string_buffer(64)
        cl.clGetPlatformInfo(c_void_p(platform), PLATFORM_NAME, c_size_t(64), name, None)
        if name.value == b"rusticl":
            rusticl = c_void_p(platform)
    if rusticl is None:
        raise Failed("Mesa's OpenCL platform, rusticl, isn't there.")

    device = c_void_p()
    if cl.clGetDeviceIDs(rusticl, c_uint64(DEVICE_TYPE_ALL), 1, byref(device), None):
        raise Failed("rusticl has no device. That happens when libclc isn't where Mesa looks for it.")

    error = c_int32()
    context = c_void_p(cl.clCreateContext(None, 1, byref(device), None, None, byref(error)))
    check("Creating a context", error.value)
    source = ctypes.c_char_p(SOURCE)
    program = c_void_p(cl.clCreateProgramWithSource(context, 1, byref(source), None, byref(error)))
    check("Loading the kernel", error.value)
    if cl.clBuildProgram(program, 1, byref(device), None, None, None):
        log = ctypes.create_string_buffer(1 << 16)
        cl.clGetProgramBuildInfo(program, device, PROGRAM_BUILD_LOG, c_size_t(1 << 16), log, None)
        raise Failed("The kernel didn't compile:\n" + log.value.decode(errors="replace"))

    numbers = (c_float * len(NUMBERS))(*NUMBERS)
    size = c_size_t(ctypes.sizeof(numbers))
    flags = c_uint64(MEM_READ_WRITE | MEM_COPY_HOST_PTR)
    buffer = c_void_p(cl.clCreateBuffer(context, flags, size, numbers, byref(error)))
    check("Creating a buffer", error.value)
    kernel = c_void_p(cl.clCreateKernel(program, b"root_plus_one", byref(error)))
    check("Creating the kernel", error.value)
    check("Setting its argument", cl.clSetKernelArg(kernel, 0, c_size_t(ctypes.sizeof(buffer)), byref(buffer)))
    queue = c_void_p(cl.clCreateCommandQueueWithProperties(context, device, None, byref(error)))
    check("Creating a queue", error.value)
    work = (c_size_t * 1)(len(NUMBERS))
    check("Running the kernel", cl.clEnqueueNDRangeKernel(queue, kernel, 1, None, work, None, 0, None, None))
    result = (c_float * len(NUMBERS))()
    check("Reading the result", cl.clEnqueueReadBuffer(queue, buffer, 1, c_size_t(0), size, result, 0, None, None))
    return list(result)


def main():
    try:
        result = run_kernel()
    except (OSError, Failed) as error:
        print(f"OpenCL doesn't work: {error}", file=sys.stderr)
        return 1
    if any(abs(got - want) > 1e-4 for got, want in zip(result, EXPECTED, strict=True)):
        print(f"OpenCL doesn't work: the kernel returned {result} instead of {EXPECTED}.", file=sys.stderr)
        return 1
    print("OpenCL works: rusticl ran a test kernel on llvmpipe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
