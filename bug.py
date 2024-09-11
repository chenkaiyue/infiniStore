import _infinity
import torch
import ctypes

import cupy
import cupy.cuda.runtime as runtime


conn = _infinity.Connection()
if _infinity.init_connection(conn) < 0:
    raise Exception("Failed to initialize connection")

key = "example_key"
src_tensor = torch.tensor([7, 9, 8], device="cuda", dtype=torch.float32)


ipc_handle1 = runtime.ipcGetMemHandle(src_tensor.data_ptr())
runtime.ipcGetMemHandle(src_tensor.data_ptr())

print(f"ipc_handle = {ipc_handle1}")


if src_tensor.device.type != "cuda":
    raise Exception("Tensor must be on CUDA device")

#import pdb; pdb.set_trace()
assert src_tensor.is_contiguous()
OP_R="R"
OP_W="W"

ret = _infinity.rw_local(conn, OP_W, key, src_tensor.data_ptr(), 0, src_tensor.numel() * src_tensor.element_size())


print(f"write tensor {src_tensor} to infinity with key {key}, ret = {ret}")



dst_tensor = torch.tensor([0, 0, 0], device="cuda", dtype=torch.float32)

ipc_handle2 = runtime.ipcGetMemHandle(dst_tensor.data_ptr())
print(f"ipc_handle = {ipc_handle2}")

#compare ipc_handle1 and ipc_handle2
if ipc_handle1 == ipc_handle2:
    print("BUGON: ipc_handle1 and ipc_handle2 are the SAME!")

ret = _infinity.rw_local(conn, OP_R, key, dst_tensor.data_ptr(), 0, dst_tensor.numel() * dst_tensor.element_size())
print(f"read tensor {dst_tensor} from infinity with key {key} ret = {ret}")

assert torch.equal(src_tensor, dst_tensor)
_infinity.close_connection(conn)
