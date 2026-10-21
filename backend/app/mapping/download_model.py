"""Download the embedding model into the cache directory, so a container image can carry it.

    python -m app.mapping.download_model

Run during the image build (see backend/Dockerfile). At run time the model is read from the cache and nothing is fetched.
"""

from app.mapping.embedding import DIMENSIONS, EmbeddingService


def main() -> None:
    service = EmbeddingService()
    vectors = service.encode(["download check"])
    assert vectors.shape == (1, DIMENSIONS)
    print(f"{service.model_name} is cached in {service.cache_dir} ({DIMENSIONS} dimensions)")


if __name__ == "__main__":
    main()
