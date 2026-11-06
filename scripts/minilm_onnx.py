"""Local all-MiniLM-L6-v2 encoder for the retrieval experiment."""

import numpy as np
from onnxruntime import InferenceSession, SessionOptions
from tokenizers import Tokenizer


def load_encoder(model_dir):
    tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=256)
    tokenizer.enable_padding()
    options = SessionOptions()
    options.intra_op_num_threads = 1
    session = InferenceSession(
        str(model_dir / "model.onnx"), options, providers=["CPUExecutionProvider"]
    )
    input_names = {item.name for item in session.get_inputs()}

    def encode(texts):
        tokens = tokenizer.encode_batch(texts)
        mask = np.array([item.attention_mask for item in tokens], dtype=np.int64)
        inputs = {
            "input_ids": np.array([item.ids for item in tokens], dtype=np.int64),
            "attention_mask": mask,
            "token_type_ids": np.array([item.type_ids for item in tokens], dtype=np.int64),
        }
        hidden, = session.run(
            ["last_hidden_state"], {name: inputs[name] for name in input_names}
        )
        pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        return pooled / np.linalg.norm(pooled, axis=1, keepdims=True)

    return encode
