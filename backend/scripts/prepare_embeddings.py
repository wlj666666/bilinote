"""Explicit one-time provisioning; Compose keeps downloads out of user requests."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['EMBEDDING_ALLOW_DOWNLOAD'] = '1'

from app.services.local_embeddings import get_embedding_function

if __name__ == '__main__':
    embedding = get_embedding_function()
    print(f'Embedding 模型准备完成，可离线使用: {embedding.DOWNLOAD_PATH}')
