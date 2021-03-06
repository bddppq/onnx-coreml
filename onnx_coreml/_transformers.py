from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import numpy as np

from ._graph import Graph, Node


class NodesFuser(object):
    '''
    An abstract helper for merging nodes
    '''
    def __init__(self, num_nodes):
        self.num_nodes = num_nodes

    def __call__(self, graph):
        nodes = graph.nodes
        merged_nodes = {}
        for node in nodes:
            nodes_window = []
            n = node
            for _ in range(self.num_nodes - 1):
                if len(n.parents) != 1:
                    # We're only fusing nodes with single parents
                    break
                p = n.get_only_parent()
                if len(p.children) != 1:
                    # We can only fuse a node if its parent's
                    # value isn't used by any other node.
                    break
                nodes_window.insert(0, n)
                n = p
            if len(nodes_window) > 0:
                # add parent of chained nodes
                first = nodes_window[0]
                p = first.get_only_parent()
                if len(p.children) == 1:
                    nodes_window.insert(0, p)
            if len(nodes_window) != self.num_nodes:
                continue
            if not self.is_eligible(graph, nodes_window):
                continue
            merged = self.merge(graph, nodes_window)
            first, last = nodes_window[0], nodes_window[-1]
            for parent in first.parents:
                parent.children.remove(first)
                if merged[0] not in parent.children:
                    parent.add_child(merged[0])
            for child in last.children:
                child.parents.remove(last)
                if merged[-1] not in child.parents:
                    child.add_parent(merged[-1])
            for n in nodes_window:
                merged_nodes[n.name] = merged

        transformed_nodes = []
        added_merged = []
        for node in nodes:
            if node.name in merged_nodes:
                merged = merged_nodes[node.name]
                if merged[0] not in added_merged:
                    for n in merged:
                        transformed_nodes.append(n)
                    added_merged.append(merged[0])
            else:
                transformed_nodes.append(node)
        return Graph(transformed_nodes, graph.inputs, graph.outputs)

    def is_eligible(self, graph, nodes):
        '''Returns true if this subset of nodes is eligible for fusion.'''
        raise NotImplementedError('Must be implemented by subclass.')

    def merge(self, graph, nodes):
        '''Merge nodes'''
        nodes[0].outputs = nodes[-1].outputs
        return [nodes[0]]


class ConvAddFuser(NodesFuser):
    '''
    Fuses Add layer into parent convolution layer.
    '''
    def __init__(self):
        super(ConvAddFuser, self).__init__(2)

    def is_eligible(self, graph, nodes):
        parent, child = nodes[0], nodes[1]
        if parent.op_type != 'Conv':
            return False
        if child.op_type != 'Add':
            return False
        if 'broadcast' not in child.attrs:
            return False
        if 'axis' not in child.attrs:
            return False

        broadcast = child.attrs['broadcast']
        if broadcast != 1:
            return False

        axis = child.attrs['axis']
        if axis != 1:
            return False

        return True

    def merge(self, graph, nodes):
        parent, child = nodes[0], nodes[1]
        output_channels = parent.input_tensors[parent.inputs[1]].shape[0]
        if len(parent.inputs) > 2:
            bias_input_name = parent.inputs[2]
            bias = parent.input_tensors[bias_input_name]
        else:
            bias_input_name = "{}_bias".format(parent.name,)
            parent.inputs.append(bias_input_name)
            bias = np.zeros(
                (output_channels,), dtype=np.float32
            )
            parent.input_tensors[bias_input_name] = bias
        bias = bias + child.input_tensors[child.inputs[1]]
        parent.input_tensors[bias_input_name] = bias
        parent.outputs = child.outputs
        parent.children.remove(child)
        child.parents.remove(parent)
        return [parent]


class BNBroadcastedMulFuser(NodesFuser):
    '''
    Fuses Mul into BatchNorm
    '''
    def __init__(self):
        super(BNBroadcastedMulFuser, self).__init__(2)

    def is_eligible(self, graph, nodes):
        parent, child = nodes[0], nodes[1]
        if parent.op_type != 'BatchNormalization':
            return False
        if child.op_type != 'Mul':
            return False
        if "broadcast" not in child.attrs:
            return False
        if child.attrs["broadcast"] != 1:
            return False
        if "axis" not in child.attrs:
            return False
        if child.attrs["axis"] != 1:
            return False
        if child.inputs[1] not in child.input_tensors:
            return False
        return True

    def merge(self, graph, nodes):
        parent, child = nodes[0], nodes[1]
        weight = parent.input_tensors[parent.inputs[1]]
        bias = parent.input_tensors[parent.inputs[2]]
        W = child.input_tensors[child.inputs[1]]
        parent.input_tensors[parent.inputs[1]] = np.multiply(weight, W)
        parent.input_tensors[parent.inputs[2]] = np.multiply(bias, W)
        parent.outputs = child.outputs
        parent.children.remove(child)
        child.parents.remove(parent)
        return [parent]


class BNBroadcastedAddFuser(NodesFuser):
    '''
    Fuses Add into BatchNorm
    '''
    def __init__(self):
        super(BNBroadcastedAddFuser, self).__init__(2)

    def is_eligible(self, graph, nodes):
        parent, child = nodes[0], nodes[1]
        if parent.op_type != 'BatchNormalization':
            return False
        if child.op_type != 'Add':
            return False
        if "broadcast" not in child.attrs:
            return False
        if child.attrs["broadcast"] != 1:
            return False
        if "axis" not in child.attrs:
            return False
        if child.attrs["axis"] != 1:
            return False
        if len(child.inputs) != 2:
            return False
        if child.inputs[1] not in child.input_tensors:
            return False
        return True

    def merge(self, graph, nodes):
        parent, child = nodes[0], nodes[1]
        bias = parent.input_tensors[parent.inputs[2]]
        b = child.input_tensors[child.inputs[1]]
        parent.input_tensors[parent.inputs[2]] = bias + b
        parent.outputs = child.outputs
        parent.children.remove(child)
        child.parents.remove(parent)
        return [parent]


class DropoutRemover(NodesFuser):
    '''
    Removes Dropout layer
    '''
    def __init__(self):
        super(DropoutRemover, self).__init__(2)

    def is_eligible(self, graph, nodes):
        child = nodes[1]
        return child.op_type == "Dropout"

    def merge(self, graph, nodes):
        parent, child = nodes[0], nodes[1]
        parent.children.remove(child)
        child.parents.remove(parent)
        parent.outputs = child.outputs
        return [parent]


class ReshapeInitTensorFuser(object):
    '''
    Fuses Reshape operator if it is used only to reshape blob in
    graph initializer. We can reshape here instead of runtime.
    '''

    def __call__(self, graph):
        nodes = graph.nodes
        removed = []
        for node in nodes:
            if node.op_type != 'Reshape':
                continue
            if len(node.input_tensors) != 1:
                continue
            tensor_name = node.input_tensors.keys()[0]
            if tensor_name != node.inputs[0]:
                continue
            assert len(node.parents) == 0

            removed.append(node)
            output_name = node.outputs[0]

            tensor = node.input_tensors[tensor_name]
            shape = tuple(node.attrs["shape"])
            reshaped_tensor = tensor.reshape(shape)

            for child in node.children:
                child.parents.remove(node)
                child.input_tensors[output_name] = reshaped_tensor

        transformed_nodes = [node for node in nodes if node not in removed]
        return Graph(transformed_nodes, graph.inputs, graph.outputs)


class DanglingOutputsRemover(object):
    '''
    Removes unused outputs
    '''

    def __call__(self, graph):
        nodes = graph.nodes
        graph_output_names = set([o[0] for o in graph.outputs])
        for node in nodes:
            removed_outputs = set()
            for output in node.outputs:
                if output in graph_output_names:
                    continue
                children_inputs = set()
                for child in node.children:
                    for input_ in child.inputs:
                        children_inputs.add(input_)
                if output in children_inputs:
                    continue
                removed_outputs.add(output)
            node.outputs = [out for out in node.outputs
                            if out not in removed_outputs]
        return graph


class OutputRenamer(object):
    '''
    Rename outputs according to mapping
    '''
    def __init__(self, mapping):
        self.mapping = mapping

    def __call__(self, graph):
        mapping = self.mapping.copy()
        nodes = graph.nodes
        for node in nodes:
            for i in range(len(node.outputs)):
                output = node.outputs[i]
                if output not in mapping:
                    continue
                node.outputs[i] = mapping[output]
                for child in node.children:
                    for j in range(len(child.inputs)):
                        input_ = child.inputs[j]
                        if input_ != output:
                            continue
                        child.inputs[j] = mapping[output]
                del mapping[output]
                if len(mapping) == 0:
                    break
        return graph


class PixelShuffleFuser(NodesFuser):
    '''
    Fuses 3 operators reshape->transpose->reshape which is equivalent to
    pytorch's pixel_shuffle layer
    '''
    def __init__(self):
        super(PixelShuffleFuser, self).__init__(3)
        self.num_added = 0

    def is_eligible(self, graph, nodes):
        if nodes[0].op_type != 'Reshape':
            return False
        if nodes[1].op_type != 'Transpose':
            return False
        if nodes[2].op_type != 'Reshape':
            return False

        shape = nodes[0].attrs['shape']
        if len(shape) != 6:
            return False
        if shape[0] != 1 or shape[2] != shape[3]:
            return False

        input_channels = shape[1]
        scale_factor = shape[2]
        input_height = shape[4]
        input_width = shape[5]

        if nodes[1].attrs.get('perm', []) != [0, 1, 4, 2, 5, 3]:
            return False

        shape = nodes[2].attrs['shape']
        if len(shape) != 4:
            return False

        output_channels = shape[1]
        output_height = shape[2]
        output_width = shape[3]
        if input_channels != output_channels:
            return False
        if (input_height * scale_factor) != output_height:
            return False
        if (input_width * scale_factor) != output_width:
            return False

        return True

    def get_unique_edge_name(self, graph, name):
        self.num_added += 1
        return graph.get_unique_edge_name(name + '_' + str(self.num_added))

    def merge(self, graph, nodes):
        '''
        Pixel shuffle is implemented using 3 operators:
            - Reshape(1, channels, scale, scale, height, width)
            - Transpose(0, 1, 4, 2, 5, 3)
            - Reshape(1, channels, height * scale, width * scale)
        CoreML Reshape and Transpose layers don't support tensors with more
        than 4 dimensions. Thus we change above sequence of operators to the
        following equivalent sequence:
            - Reshape(channels, scale * scale, height, width)
            - Transpose(0, 2, 1, 3)
            - Reshape(channels * height, scale, scale, width)
            - Transpose(0, 1, 3, 2)
            - Reshape(1, channels, height * scale, width * scale)
        '''
        reshape_1 = nodes[0]
        transpose_1 = nodes[1]
        transpose_1.children = []

        shape = reshape_1.attrs['shape']

        channels = shape[1]
        scale = shape[2]
        height = shape[4]
        width = shape[5]

        reshape_1.attrs['shape'] = [channels, scale * scale, height, width]
        transpose_1.attrs['perm'] = [0, 2, 1, 3]

        reshape_output_name = 'pixel_shuffle_reshape'
        transpose_output_name = 'pixel_shuffle_transpose'

        transpose_1.outputs = [
            self.get_unique_edge_name(graph, transpose_output_name)
        ]

        reshape_2 = Node(
            reshape_output_name,
            'Reshape',
            {'shape': [channels * height, scale, scale, width]},
            transpose_1.outputs,
            [self.get_unique_edge_name(graph, reshape_output_name)]
        )
        transpose_1.add_child(reshape_2)

        transpose_2 = Node(
            transpose_output_name,
            'Transpose',
            {'perm': [0, 1, 3, 2]},
            reshape_2.outputs,
            [self.get_unique_edge_name(graph, transpose_output_name)]
        )
        reshape_2.add_child(transpose_2)

        final_reshape = nodes[2]
        final_reshape.inputs = transpose_2.outputs
        final_reshape.parents = []
        transpose_2.add_child(final_reshape)
        return [reshape_1, transpose_1, reshape_2, transpose_2, final_reshape]
