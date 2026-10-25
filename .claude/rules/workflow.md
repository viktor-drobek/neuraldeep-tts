# Delivery workflow

Start each observable change with a failing offline test. Implement from validation upward. Keep source contract documents aligned and sync the distribution only from committed source changes. No main pushes or PR merges without the owner's decision. Run the complete offline suite, CLI help and git diff --check before reporting success.
