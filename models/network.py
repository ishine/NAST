import torch
from torch import nn
import torchaudio
import torch.nn.functional as F
from typing import Dict, Any

class Network(nn.Module):
    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self.config = config
        self.in_channels = config["in_channels"]
        self.num_units = config["num_units"]
        self.vocab_size = config["num_units"]
        self.quantize = config["quantize"]
        self.out_channels = 768 if config["reconstruction_type"] == "HuBERT" else None
        self.use_global_residual = config["use_global_residual"]
        self.hubert_speaker = config["hubert_speaker"]

        self.predictor = torchaudio.models.Conformer(
            input_dim=self.in_channels,
            num_heads=config["predictor"]["num_heads"],
            ffn_dim=config["predictor"]["ffn_dim"],
            num_layers=config["predictor"]["num_layers"],
            depthwise_conv_kernel_size=config["predictor"]["depthwise_conv_kernel_size"]
        )
        
        self.projector = nn.Linear(self.in_channels, self.num_units)

        self.residual_encoder = torchaudio.models.Conformer(
            input_dim=self.in_channels,
            num_heads=config["residual_encoder"]["num_heads"],
            ffn_dim=config["residual_encoder"]["ffn_dim"],
            num_layers=config["residual_encoder"]["num_layers"],
            depthwise_conv_kernel_size=config["residual_encoder"]["depthwise_conv_kernel_size"]
        )
        
        self.residual_projector = nn.Linear(768, 256)

        self.decoder = torchaudio.models.Conformer(
            input_dim=self.num_units + 256,
            num_heads=config["decoder"]["num_heads"],
            ffn_dim=config["decoder"]["ffn_dim"],
            num_layers=config["decoder"]["num_layers"],
            depthwise_conv_kernel_size=config["decoder"]["depthwise_conv_kernel_size"]
        )
        
        self.decoder_projection = nn.Linear(self.num_units + 256, self.out_channels)

    def forward(self, input_features: torch.Tensor) -> torch.Tensor:
        """
        Args:
            input_features (torch.Tensor): Input features tensor.
        Returns:
            torch.Tensor: reconstructed representation, one hot vector, logits before gumbel operation
        """
        if self.quantize:
            return self.quantize_forward(input_features)

        residual_information = self.get_residual_information(input_features)
        predicts, one_hot_vector = self.get_predictions(input_features)

        x = torch.cat([one_hot_vector, residual_information], 1)

        output = self.decoder(torch.unsqueeze(x, dim=0), torch.tensor([x.shape[0]]).to(x.device))
        output = self.decoder_projection(torch.squeeze(output[0], dim=0))

        if self.hubert_speaker:
            return output
        else:
            return output, one_hot_vector, predicts

    def quantize_forward(self, input_features):
        predicts = self.predictor(torch.unsqueeze(input_features, dim=0), torch.tensor([input_features.shape[0]]).to(input_features.device))
        predicts = self.projector(torch.squeeze(predicts[0], dim=0))
        one_hot_vector = F.gumbel_softmax(logits=predicts, tau=0.8, hard=False, dim=1)
        return torch.argmax(one_hot_vector, dim=1)

    def get_predictions(self, input_features):
        predicts = self.predictor(torch.unsqueeze(input_features, dim=0), torch.tensor([input_features.shape[0]]).to(input_features.device))
        predicts = self.projector(torch.squeeze(predicts[0], dim=0))
        one_hot_vector = F.gumbel_softmax(logits=predicts, tau=0.8, hard=False, dim=1)
        return predicts, one_hot_vector

    def get_residual_information(self, input_features):
        residual_information = self.residual_encoder(torch.unsqueeze(input_features, dim=0), torch.tensor([input_features.shape[0]]).to(input_features.device))
        residual_information = self.residual_projector(torch.squeeze(residual_information[0], dim=0))
        if self.use_global_residual:
            residual_information = torch.sum(residual_information, 0) / residual_information.shape[0]
        else:
            residual_information = residual_information
        residual_information = residual_information.repeat(input_features.shape[0], 1).requires_grad_()
        return residual_information
