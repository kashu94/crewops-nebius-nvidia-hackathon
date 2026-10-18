"""Semantic search storage: one S3 Vectors bucket with three small indexes.

Rules, gold example questions and past scenarios are embedded with Titan Text Embeddings V2
into fixed-size vectors. Each index stores the embedded text as non-filterable metadata, so
a query can score the same documents by keywords too. There is no idle cost: S3 Vectors
charges for storage and queries only.
"""

from __future__ import annotations

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_s3vectors as s3vectors
from constructs import Construct

from crewops.config import Settings
from crewops.search.vectors import INDEXES, TEXT_KEY


class SearchStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, settings: Settings, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.bucket = s3vectors.CfnVectorBucket(
            self, "VectorBucket", vector_bucket_name=settings.vector_bucket
        )
        self.indexes = {}
        for name in INDEXES:
            index = s3vectors.CfnIndex(
                self,
                f"{name.capitalize()}Index",
                vector_bucket_name=settings.vector_bucket,
                index_name=name,
                data_type="float32",
                dimension=settings.embedding_dimensions,
                distance_metric="cosine",
                metadata_configuration=s3vectors.CfnIndex.MetadataConfigurationProperty(
                    non_filterable_metadata_keys=[TEXT_KEY]
                ),
            )
            index.add_dependency(self.bucket)
            self.indexes[name] = index

        CfnOutput(self, "VectorBucketName", value=settings.vector_bucket)
        CfnOutput(self, "VectorBucketArn", value=self.bucket.attr_vector_bucket_arn)
