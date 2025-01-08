# standard library
from pathlib import Path
from typing import *
import sys, os
# third party
import numpy as np
import torch
from PIL import Image
from mmengine import Config
# metric 3d
metric3d_path = Path(__file__).resolve().parent / 'metric3d'
metric3d_mono_path = metric3d_path / 'mono'
sys.path.append(str(metric3d_path))
sys.path.append(str(metric3d_mono_path))
from .metric3d.mono.model.monodepth_model import get_configured_monodepth_model
from .metric3d.mono.mono_utils.running import load_ckpt
from .metric3d.mono.mono_utils.do_test import transform_test_data_scalecano, get_prediction
from .metric3d.mono.mono_utils.mldb import load_data_info, reset_ckpt_path
from .metric3d.mono.mono_utils.transform import gray_to_colormap

__ALL__ = ['Metric3D']

class Metric3D:
    cfg_: Config
    model_: torch.nn.Module

    def __init__(
        self,
        checkpoint: Union[str, Path] = './weights/metric_depth_vit_large_800k.pth',
        model_name: str = 'v2-L',
    ) -> None:
        checkpoint = Path(checkpoint).resolve()
        cfg:Config = self._load_config_(model_name, checkpoint)
        # build model
        model = get_configured_monodepth_model(cfg, )
        model = torch.nn.DataParallel(model).cuda()
        model, _, _, _ = load_ckpt(cfg.load_from, model, strict_match=False)
        model.eval()
        # save to self
        self.cfg_ = cfg
        self.model_ = model

    @torch.no_grad()
    def __call__(
        self,
        rgb_image: Union[np.ndarray, Image.Image, str, Path],
        intrinsic: Union[str, Path, np.ndarray],
        d_max: Optional[float] = 300,
        d_min: Optional[float] = 0
    ) -> np.ndarray:
        # read image
        if isinstance(rgb_image, (str, Path)):
            rgb_image = np.array(Image.open(rgb_image))
        elif isinstance(rgb_image, Image.Image):
            rgb_image = np.array(rgb_image)
        # get intrinsic
        h, w = rgb_image.shape[:2]
        if isinstance(intrinsic, (str, Path)):
            intrinsic = np.loadtxt(intrinsic)
        intrinsic = intrinsic[:4]
        # transform image
        rgb_input, cam_models_stacks, pad, label_scale_factor = \
            transform_test_data_scalecano(rgb_image, intrinsic, self.cfg_.data_basic)
        # predict depth
        # TTD 2024/08/20
        rgb_input = rgb_input.unsqueeze(0)
        
        normalize_scale = self.cfg_.data_basic.depth_range[1]
        # pred_depth, _, scale, output = get_prediction(
        pred_depth, _ = get_prediction(
            model = self.model_,
            input = rgb_input,
            cam_model = cam_models_stacks,
            pad_info = pad,
            scale_info = label_scale_factor,
            gt_depth = None,
            normalize_scale = normalize_scale,
            ori_shape=[h, w],
        )
        # post process
        # pred_depth = (pred_depth > 0) * (pred_depth < 300) * pred_depth
        pred_depth = pred_depth.squeeze().cpu().numpy()
        pred_depth[pred_depth > d_max] = 0
        pred_depth[pred_depth < d_min] = 0
        return pred_depth

    def _load_config_(
        self,
        model_name: str,
        checkpoint: Union[str, Path],
    ) -> Config:
        print(f'Loading model {model_name} from {checkpoint}')
        config_path = metric3d_path / 'mono/configs/HourglassDecoder'
        assert model_name in ['v2-L', 'v2-S', 'v2-g'], f"Model {model_name} not supported"
        # load config file
        cfg = Config.fromfile(
            str(config_path / 'vit.raft5.large.py') if model_name == 'v2-L' 
            else str(config_path / 'vit.raft5.small.py') if model_name == 'v2-S' 
            else str(config_path / 'vit.raft5.giant2.py')
        )
        cfg.load_from = str(checkpoint)
        # load data info
        data_info = {}
        load_data_info('data_info', data_info=data_info)
        cfg.mldb_info = data_info
        # update check point info
        reset_ckpt_path(cfg.model, data_info)
        # set distributed
        cfg.distributed = False
        
        return cfg
    
    @staticmethod
    def gray_to_colormap(depth: np.ndarray) -> np.ndarray:
        return gray_to_colormap(depth)