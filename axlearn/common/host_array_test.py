# Copyright © 2023 Apple Inc.

"""Tests global-host array conversions.

Some tests are intended to be run on TPU.
"""
import itertools

import jax
import numpy as np
import pytest
from absl import logging
from absl.testing import absltest, parameterized
from jax import numpy as jnp
from jax.experimental import mesh_utils

from axlearn.common.test_utils import TestCase, is_supported_mesh_shape, is_supported_platform
from axlearn.common.utils import (
    DataPartitionType,
    flatten_items,
    global_to_host_array,
    host_to_global_device_array,
)


def is_supported(
    platform: str,
    mesh_shape: tuple[int, int],
    global_batch_size: int,
    data_partition: DataPartitionType,
):
    if not is_supported_platform(platform):
        return False, f'Platform "{platform}" not supported with devices {jax.devices()}.'
    if not is_supported_mesh_shape(mesh_shape):
        return (
            False,
            f'Mesh shape "{mesh_shape}" not supported with device_count "{jax.device_count()}".',
        )
    if data_partition != DataPartitionType.REPLICATED:
        return (
            False,
            f'Data partition is "{data_partition}", expected "DataPartitionType.REPLICATED".',
        )
    if global_batch_size % jax.device_count() != 0:
        return (
            False,
            (
                "Global batch has to be divisible with number of devices. Global "
                f'batch is "{global_batch_size}", number of devices is "{jax.device_count()}".',
            ),
        )
    return True, ""


class HostArrayTest(TestCase):
    @parameterized.parameters(
        itertools.product(
            ("cpu", "tpu"),  # platform,
            ((1, 1), (4, 1), (2, 2), (8, 1), (4, 2)),  # mesh_shape
            (1, 16),  # global_batch_size
            (DataPartitionType.FULL, DataPartitionType.REPLICATED),  # data_partition
        )
    )
    def test_global_host_array_conversion(
        self,
        platform: str,
        mesh_shape: tuple[int, int],
        global_batch_size: int,
        data_partition: DataPartitionType,
    ):
        logging.info(
            "platform=%s mesh_shape=%s global_batch_size=%s data_partition=%s",
            platform,
            mesh_shape,
            global_batch_size,
            data_partition,
        )
        supported, reason = is_supported(platform, mesh_shape, global_batch_size, data_partition)
        if not supported:
            pytest.skip(reason)
        devices = mesh_utils.create_device_mesh(mesh_shape)
        mesh = jax.sharding.Mesh(devices, ("data", "model"))
        logging.info("Global mesh: %s", mesh)
        with mesh:
            if data_partition == DataPartitionType.REPLICATED:
                process_batch_size = global_batch_size
                x_start = 0
            else:
                process_batch_size = global_batch_size // jax.process_count()
                x_start = process_batch_size * jax.process_index()
            host_arrays = dict(x=x_start + jnp.arange(process_batch_size))
            global_arrays = host_to_global_device_array(host_arrays, partition=data_partition)
            for path, value in flatten_items(global_arrays):
                self.assertEqual(global_batch_size, value.shape[0], msg=path)
            global_arrays["y"] = 2 * global_arrays["x"]
            restored_host_arrays = global_to_host_array(global_arrays, partition=data_partition)
            for path, restored_value in flatten_items(restored_host_arrays):
                restored_batch_size = restored_value.shape[0]
                self.assertEqual(process_batch_size, restored_batch_size, msg=path)

            # "x" and "y" are partitioned consistently.
            np.testing.assert_array_equal(restored_host_arrays["y"], 2 * restored_host_arrays["x"])

            # Check round-trip equality of host_to_global_device_array and global_to_host_array.
            np.testing.assert_array_equal(host_arrays["x"], restored_host_arrays["x"])


if __name__ == "__main__":
    absltest.main()
