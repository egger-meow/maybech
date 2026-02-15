import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from src.config.strategy import StrategyConfig, MomentumConfig

def test_config_loading():
    print("Testing Config Loading...")
    
    # Test Default
    print("1. Testing defaults...")
    cfg = StrategyConfig.default()
    assert isinstance(cfg.momentum, MomentumConfig)
    assert cfg.momentum.stop_win_ratio == 1.0
    print("   Defaults OK:", cfg)

    # Test Save and Load
    print("2. Testing Save and Load...")
    cfg.momentum.k_long = 99.9
    cfg.momentum.stop_win_ratio = 2.5
    cfg.save()
    
    loaded_cfg = StrategyConfig.load()
    assert loaded_cfg.momentum.k_long == 99.9
    assert loaded_cfg.momentum.stop_win_ratio == 2.5
    print("   Save/Load OK:", loaded_cfg)
    
    # Test MomentumStrategy Instantiation
    print("3. Testing MomentumStrategy Instantiation...")
    from src.strategies.momentum import MomentumStrategy
    strategy = MomentumStrategy(config=cfg.momentum)
    assert strategy.k_long == cfg.momentum.k_long
    print("   MomentumStrategy Instantiated OK.")

    # Restore default for cleanliness
    print("4. Restoring defaults...")
    default_cfg = StrategyConfig.default()
    default_cfg.save()
    print("   Restored.")

if __name__ == "__main__":
    try:
        test_config_loading()
        print("\nSUCCESS: Strategy Config Refactor Verified.")
    except Exception as e:
        print(f"\nFAILURE: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
