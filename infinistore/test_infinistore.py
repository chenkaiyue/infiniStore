import torch
import pytest
import infinistore
import time
import os
import signal
import subprocess
import random
import string
import contextlib


# Fixture to start the TCzpserver before running tests
@pytest.fixture(scope="module")
def server():
    server_process = subprocess.Popen(["python", "-m", "infinistore.server"])
    time.sleep(4)
    yield
    os.kill(server_process.pid, signal.SIGINT)
    server_process.wait()


# add a flat to wehther the same connection.


def generate_random_string(length):
    letters_and_digits = string.ascii_letters + string.digits  # 字母和数字的字符集
    random_string = "".join(random.choice(letters_and_digits) for i in range(length))
    return random_string


def get_gpu_count():
    if torch.cuda.is_available():
        gpu_count = torch.cuda.device_count()
        return gpu_count
    else:
        return 0


@pytest.mark.parametrize("dtype", [torch.float16, torch.float32])
@pytest.mark.parametrize("new_connection", [True, False])
@pytest.mark.parametrize("local", [True, False])
def test_basic_read_write_cache(server, dtype, new_connection, local):
    config = infinistore.ClientConfig(
        host_addr="127.0.0.1", service_port=22345, dev_name="mlx5_0"
    )
    config.connection_type = (
        infinistore.TYPE_LOCAL_GPU if local else infinistore.TYPE_RDMA
    )

    conn = infinistore.InfinityConnection(config)
    conn.connect()

    # key is random string
    key = generate_random_string(10)
    src = [i for i in range(4096)]

    # local GPU write is tricky, we need to disable the pytorch allocator's caching
    with infinistore.DisableTorchCaching() if local else contextlib.nullcontext():
        src_tensor = torch.tensor(src, device="cuda:0", dtype=dtype)

    conn.write_cache(src_tensor, [(key, 0)], 4096)
    conn.sync()

    conn = infinistore.InfinityConnection(config)
    conn.connect()

    with infinistore.DisableTorchCaching() if local else contextlib.nullcontext():
        dst = torch.zeros(4096, device="cuda:0", dtype=dtype)
    conn.read_cache(dst, [(key, 0)], 4096)
    conn.sync()
    assert torch.equal(src_tensor, dst)


@pytest.mark.parametrize("seperated_gpu", [True, False])
@pytest.mark.parametrize("local", [True, False])
def test_batch_read_write_cache(server, seperated_gpu, local):
    config = infinistore.ClientConfig(
        host_addr="127.0.0.1",
        service_port=22345,
    )
    config.connection_type = (
        infinistore.TYPE_LOCAL_GPU if local else infinistore.TYPE_RDMA
    )
    # test if we have multiple GPUs
    if seperated_gpu:
        if get_gpu_count() >= 2:
            src_device = "cuda:0"
            dst_device = "cuda:1"
        else:
            # skip if we don't have enough GPUs
            return
    else:
        src_device = "cuda:0"
        dst_device = "cuda:0"

    conn = infinistore.InfinityConnection(config)
    conn.connect()

    num_of_blocks = 10
    keys = [generate_random_string(num_of_blocks) for i in range(10)]
    block_size = 4096
    src = [i for i in range(num_of_blocks * block_size)]

    with infinistore.DisableTorchCaching() if local else contextlib.nullcontext():
        src_tensor = torch.tensor(src, device=src_device, dtype=torch.float32)

    blocks = [(keys[i], i * block_size) for i in range(num_of_blocks)]

    conn.write_cache(src_tensor, blocks, block_size)
    conn.sync()

    with infinistore.DisableTorchCaching() if local else contextlib.nullcontext():
        dst = torch.zeros(
            num_of_blocks * block_size, device=dst_device, dtype=torch.float32
        )

    conn.read_cache(dst, blocks, block_size)
    conn.sync()
    # import pdb; pdb.set_trace()
    assert torch.equal(src_tensor.cpu(), dst.cpu())


@pytest.mark.parametrize("limited_bar1", [(True, 100 << 20), (False, 10 << 20)])
def test_read_write_bottom_cache(server, limited_bar1):
    config = infinistore.ClientConfig(
        host_addr="127.0.0.1",
        service_port=22345,
        dev_name="mlx5_0",
        connection_type=infinistore.TYPE_RDMA,
    )
    conn = infinistore.InfinityConnection(config)
    conn.connect()
    # force the limit, for GPU T4, limited_bar1 must be True
    conn.conn.limited_bar1 = limited_bar1[0]

    # allocate a 4(float32) * 100 tensor on GPU, the size is 400MB
    size = limited_bar1[1]

    src = torch.randn(size, device="cuda", dtype=torch.float32)
    key = generate_random_string(20)

    # write the bottom cache
    conn.write_cache(src, [(key, size - 512)], 512)

    conn.sync()

    # read the bottom cache
    dst = torch.zeros(512, device="cuda", dtype=torch.float32)
    conn.read_cache(dst, [(key, 0)], 512)
    conn.sync()
    assert torch.equal(src[-512:], dst)


@pytest.mark.parametrize("limited_bar1", [(True, 100 << 20), (False, 10 << 20)])
def test_read_write_interleave_cache(server, limited_bar1):
    config = infinistore.ClientConfig(
        host_addr="127.0.0.1",
        service_port=22345,
        dev_name="mlx5_0",
        connection_type=infinistore.TYPE_RDMA,
    )
    conn = infinistore.InfinityConnection(config)
    conn.connect()
    # force the limit, for GPU T4, limited_bar1 must be True
    conn.conn.limited_bar1 = limited_bar1[0]
    # allocate a 4(float32) * 100 tensor on GPU, the size is 400MB
    size = limited_bar1[1]

    src = torch.randn(size, device="cuda", dtype=torch.float32)
    key1 = generate_random_string(5)
    key2 = generate_random_string(5)

    conn.write_cache(src, [(key1, 0), (key2, size - 1024)], 1024)
    conn.sync()

    dst = torch.zeros(1024, device="cuda", dtype=torch.float32)
    conn.read_cache(dst, [(key1, 0)], 1024)
    conn.sync()
    assert torch.equal(src[0:1024], dst)

    conn.read_cache(dst, [(key2, 0)], 1024)
    conn.sync()
    assert torch.equal(src[-1024:], dst)


def test_key_check(server):
    config = infinistore.ClientConfig(
        host_addr="127.0.0.1",
        service_port=22345,
        dev_name="mlx5_0",
        connection_type=infinistore.TYPE_RDMA,
    )
    conn = infinistore.InfinityConnection(config)
    conn.connect()
    key = generate_random_string(5)
    src = torch.randn(4096, device="cuda", dtype=torch.float32)
    conn.write_cache(src, [(key, 0)], 4096)
    conn.sync()
    assert conn.check_exist(key)


def test_get_match_last_index(server):
    config = infinistore.ClientConfig(
        host_addr="127.0.0.1",
        service_port=22345,
        dev_name="mlx5_0",
        connection_type=infinistore.TYPE_RDMA,
    )
    conn = infinistore.InfinityConnection(config)
    conn.connect()
    src = torch.randn(4096, device="cuda", dtype=torch.float32)
    conn.write_cache(src, [("key1", 0), ("key2", 1024), ("key3", 2048)], 1024)
    assert conn.get_match_last_index(["A", "B", "C", "key1", "D", "E"]) == 3
