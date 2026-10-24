"""Hydra entry point for the KOL experiment harness: uv run kolproject-kol [overrides]."""
import os
import time
os.environ['TZ'] = 'UTC'
if hasattr(time, 'tzset'):
    time.tzset()
import warnings
warnings.filterwarnings('ignore', message='invalid escape sequence', category=SyntaxWarning)
import hydra
from omegaconf import DictConfig, OmegaConf
from ..common import ROOT


@hydra.main(version_base='1.3', config_path=str(ROOT / 'configs'), config_name='kol')
def main(cfg: DictConfig):
    from .run import run
    run(OmegaConf.to_container(cfg, resolve=True))


if __name__ == '__main__':
    main()
