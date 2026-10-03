"""Neural model architecture and model-delta serialization utilities.

Provides FraudMLP (emitting unnormalized logits during training) and exact
flatten/unflatten transformations between PyTorch state dicts and flat vectors.
"""

from __future__ import annotations

from collections import OrderedDict

import torch
import torch.nn as nn


class FraudMLP(nn.Module):
    """Multi-layer perceptron for financial transaction fraud classification.

    Outputs raw logits (unnormalized) for numerically stable BCEWithLogitsLoss.
    Sigmoid is applied exclusively during inference/evaluation.
    """

    def __init__(
        self,
        input_dim: int = 30,
        hidden_dims: list[int] | None = None,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        dims = hidden_dims or [64, 32]
        layers: list[nn.Module] = []
        prev_dim = input_dim

        for h_dim in dims:
            layers.append(nn.Linear(prev_dim, h_dim))
            layers.append(nn.ReLU())
            if dropout > 0:
                layers.append(nn.Dropout(p=dropout))
            prev_dim = h_dim

        layers.append(nn.Linear(prev_dim, 1))
        self.network = nn.Sequential(*layers)
        self.input_dim = input_dim
        self.hidden_dims = dims
        self.dropout = dropout

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Computes raw scalar logits."""
        return self.network(x).squeeze(-1)

    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        """Applies Sigmoid for evaluation probabilities. Never used during training."""
        with torch.no_grad():
            logits = self.forward(x)
            return torch.sigmoid(logits)


def flatten_parameters(model: nn.Module) -> torch.Tensor:
    """Extracts all trainable parameters as a single 1D contiguous tensor."""
    params: list[torch.Tensor] = []
    for p in model.parameters():
        if p.requires_grad:
            params.append(p.data.detach().reshape(-1))
    return torch.cat(params)


def unflatten_to_state_dict(flat: torch.Tensor, template_model: nn.Module) -> OrderedDict[str, torch.Tensor]:
    """Reconstructs state dictionary matching template_model parameter shapes from a 1D tensor."""
    state = OrderedDict()
    offset = 0
    flat = flat.detach()

    for name, p in template_model.named_parameters():
        if not p.requires_grad:
            continue
        numel = p.numel()
        slice_flat = flat[offset : offset + numel]
        state[name] = slice_flat.reshape(p.shape).clone()
        offset += numel

    if offset != flat.numel():
        raise ValueError(f"Tensor length mismatch: processed {offset} elements, expected {flat.numel()}")

    return state


def compute_model_delta(local_model: nn.Module, global_model: nn.Module) -> torch.Tensor:
    """Computes model delta vector: Delta_k = theta_k_local - theta_global."""
    flat_local = flatten_parameters(local_model)
    flat_global = flatten_parameters(global_model)
    return flat_local - flat_global


def apply_model_delta(global_model: nn.Module, aggregated_delta: torch.Tensor) -> None:
    """Updates global model parameters in-place: theta_global <- theta_global + Delta_agg."""
    delta_dict = unflatten_to_state_dict(aggregated_delta, global_model)
    with torch.no_grad():
        for name, p in global_model.named_parameters():
            if name in delta_dict:
                p.data.add_(delta_dict[name].to(p.device, dtype=p.dtype))
