"""Compact NAM models made up for the tests: the shape nam2zoom.app's training gives
(SlimmableContainer, one 44.1 kHz WaveNet of 14 layers, 3 channels, 659 weights), with weights
that are only numbers. They play nothing useful; they are for building banks byte for byte."""
import json

LAYERS = 14


def compact_nam(seed: int) -> bytes:
    layer = {"input_size": 1, "condition_size": 1, "channels": 3, "bottleneck": 3,
             "kernel_sizes": [3] * LAYERS, "dilations": [1, 3, 7, 17, 41, 101, 239] * 2,
             "activation": [{"type": "LeakyReLU", "negative_slope": 0.01}] * LAYERS,
             "gating_mode": ["none"] * LAYERS, "layer1x1": {"active": True, "groups": 1},
             "head": {"out_channels": 1, "kernel_size": 8, "bias": True}}
    words = 3 + LAYERS * (3 * 9 + 9 + 9) + 8 * 3 + 2           # nam2zoom.compact.expected_parameters(3)
    model = {"architecture": "WaveNet", "sample_rate": 44100,
             "config": {"layers": [layer], "head": None, "head_scale": 0.02},
             "weights": [((index * 7919 + seed * 104729) % 2001 - 1000) / 10000 for index in range(words)]}
    return json.dumps({"architecture": "SlimmableContainer", "sample_rate": 44100, "weights": [],
                       "config": {"submodels": [{"max_value": 1.0, "model": model}]}}).encode("ascii")
