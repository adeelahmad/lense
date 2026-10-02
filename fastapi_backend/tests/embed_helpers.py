"""A sentence-embedding model small enough to build in a test: its tokenizer knows a few words, and its "meaning" of a
passage is which topics its words belong to (the sea, money, medicine), so passages about the same topic are alike
whatever words they use, as with a real model."""

from __future__ import annotations

import pathlib

import numpy as np

TOPICS = {
    "sea": "harbour harbor port ship ships boat boats vessel vessels dock docks pier tide sailor sailors lighthouse sea",
    "money": "money price prices cost costs budget invoice payment payments loan cash bank funding expensive",
    "medicine": "doctor doctors nurse hospital medicine therapy patient patients clinic treatment capsid gene",
}
WORDS = ["[PAD]", "[UNK]", *sorted({w for words in TOPICS.values() for w in words.split()})]


def model_dir(folder: pathlib.Path, name: str = "tiny-topics") -> pathlib.Path:
    """Write model.onnx and tokenizer.json into folder/name and return it."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    from tokenizers import Tokenizer, models, normalizers, pre_tokenizers

    d = folder / name
    d.mkdir(parents=True, exist_ok=True)
    tok = Tokenizer(models.WordLevel({w: i for i, w in enumerate(WORDS)}, unk_token="[UNK]"))
    tok.normalizer = normalizers.Lowercase()
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.enable_padding(pad_id=0, pad_token="[PAD]")
    tok.save(str(d / "tokenizer.json"))
    # each word's vector: one axis per topic, and a faint fourth so that words of no topic aren't all zeros
    table = np.zeros((len(WORDS), 4), dtype=np.float32)
    table[1, 3] = 0.05
    for axis, words in enumerate(TOPICS.values()):
        for w in words.split():
            table[WORDS.index(w), axis] = 1.0
    graph = helper.make_graph(
        [helper.make_node("Gather", ["table", "input_ids"], ["last_hidden_state"])],
        "tiny",
        [
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["batch", "tokens"]),
            helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["batch", "tokens"]),
        ],
        [helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["batch", "tokens", 4])],
        [numpy_helper.from_array(table, "table")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(d / "model.onnx"))
    return d
