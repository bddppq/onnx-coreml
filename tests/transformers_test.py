from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import unittest
import numpy as np

from onnx import helper, numpy_helper

from onnx_coreml._graph import Graph
from onnx_coreml._transformers import ConvAddFuser
from tests._test_utils import _onnx_create_model, _test_onnx_model, \
    _conv_pool_output_size, _random_array


class ConvAddFuserTest(unittest.TestCase):
    def test_fuse_conv_without_bias(self):
        kernel_shape = (3, 2)
        strides = (2, 3)
        pads = (4, 2, 4, 2)
        dilations = (1, 2)
        group = 1
        weight = numpy_helper.from_array(
            _random_array((16, 3, 3, 2)), name="weight"
        )

        input_shape = (1, 3, 224, 224)
        output_size = _conv_pool_output_size(input_shape, dilations,
                                             kernel_shape, pads, strides)

        output_shape = (1, int(weight.dims[0]), output_size[0], output_size[1])

        inputs = [('input0', input_shape)]
        outputs = [('output0', output_shape)]

        conv = helper.make_node(
            "Conv",
            inputs=[inputs[0][0], "weight"],
            outputs=["conv_output"],
            dilations=dilations,
            group=group,
            kernel_shape=kernel_shape,
            pads=pads,
            strides=strides
        )

        b = _random_array((int(weight.dims[0]),))
        bias = numpy_helper.from_array(
            b, name="bias"
        )

        add = helper.make_node(
            "Add",
            inputs=[conv.output[0], "bias"],
            outputs=[outputs[0][0]],
            broadcast=1,
            axis=1
        )

        model = _onnx_create_model(
            [conv, add], inputs, outputs, [weight, bias]
        )
        graph_ = Graph.from_onnx(model.graph)
        fused_graph = graph_.transformed([ConvAddFuser()])

        self.assertEqual(len(fused_graph.nodes), 1)
        node = fused_graph.nodes[0]
        self.assertEqual(len(node.inputs), 3)
        np.testing.assert_equal(node.input_tensors[node.inputs[2]], b)
        self.assertEqual(fused_graph.nodes[0].outputs[0], outputs[0][0])

    def test_fuse_conv_with_bias(self):
        kernel_shape = (3, 2)
        strides = (2, 3)
        pads = (4, 2, 4, 2)
        dilations = (1, 2)
        group = 1
        weight = numpy_helper.from_array(
            _random_array((16, 3, 3, 2)), name="weight"
        )
        b = _random_array((int(weight.dims[0]),))
        bias = numpy_helper.from_array(
            b, name="bias"
        )

        input_shape = (1, 3, 224, 224)
        output_size = _conv_pool_output_size(input_shape, dilations,
                                             kernel_shape, pads, strides)

        output_shape = (1, int(weight.dims[0]), output_size[0], output_size[1])

        inputs = [('input0', input_shape)]
        outputs = [('output0', output_shape)]

        conv = helper.make_node(
            "Conv",
            inputs=[inputs[0][0], "weight", "bias"],
            outputs=["conv_output"],
            dilations=dilations,
            group=group,
            kernel_shape=kernel_shape,
            pads=pads,
            strides=strides
        )

        add = helper.make_node(
            "Add",
            inputs=[conv.output[0], "bias"],
            outputs=[outputs[0][0]],
            broadcast=1,
            axis=1
        )

        model = _onnx_create_model(
            [conv, add], inputs, outputs, [weight, bias]
        )
        graph_ = Graph.from_onnx(model.graph)
        fused_graph = graph_.transformed([ConvAddFuser()])

        self.assertEqual(len(fused_graph.nodes), 1)
        node = fused_graph.nodes[0]
        self.assertEqual(len(node.inputs), 3)
        np.testing.assert_equal(node.input_tensors[node.inputs[2]], b * 2)
        self.assertEqual(fused_graph.nodes[0].outputs[0], outputs[0][0])


class PixelShuffleFuserTest(unittest.TestCase):
    def test_pixel_shuffle(self):
        scale_factor = 2
        input_shape = (1, 8, 2, 2)
        output_shape = (
            input_shape[0],
            int(input_shape[1] / (scale_factor ** 2)),
            input_shape[2] * scale_factor,
            input_shape[3] * scale_factor
        )

        inputs = [('input0', input_shape)]
        outputs = [('output0', output_shape)]

        node_0 = helper.make_node(
            "Reshape",
            inputs=[inputs[0][0]],
            outputs=['node0'],
            shape=[
                output_shape[0],
                output_shape[1],
                scale_factor,
                scale_factor,
                input_shape[2],
                input_shape[3]
            ]
        )
        node_1 = helper.make_node(
            "Transpose",
            inputs=['node0'],
            outputs=['node1'],
            perm=[0, 1, 4, 2, 5, 3]
        )
        node_2 = helper.make_node(
            "Reshape",
            inputs=['node1'],
            outputs=[outputs[0][0]],
            shape=list(output_shape)
        )
        model = _onnx_create_model(
            [node_0, node_1, node_2], inputs, outputs
        )
        _test_onnx_model(model, decimal=7)


if __name__ == '__main__':
    unittest.main()
