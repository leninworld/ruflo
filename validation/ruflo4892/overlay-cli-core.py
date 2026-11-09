"""Same CI overlay, restricted to a new hosted container's installed package."""
from pathlib import Path
import shutil

source = Path('/work/source/v3/@claude-flow/cli-core/dist')
package = Path('/work/source/node_modules/@claude-flow/cli-core')
assert source.is_dir() and package.is_dir() and not package.is_symlink()
target = package/'dist'
assert not target.is_symlink()
if target.exists():
    shutil.rmtree(target)  # Owned ephemeral hosted Docker install only; no Mac/external data.
shutil.copytree(source, target)
