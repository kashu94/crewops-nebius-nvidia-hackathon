"""Synthesize the search stack locally (no AWS access) and check what it would create."""

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App, Environment  # noqa: E402
from aws_cdk.assertions import Template  # noqa: E402
from crewops_infra.search_stack import SearchStack  # noqa: E402

from crewops.config import Settings  # noqa: E402


def test_vector_bucket_with_three_cosine_indexes():
    settings = Settings.from_env({"CREWOPS_STAGE": "dev"})
    stack = SearchStack(
        App(),
        "Search",
        settings=settings,
        env=Environment(account="111111111111", region="ap-southeast-2"),
    )
    t = Template.from_stack(stack)
    t.has_resource_properties(
        "AWS::S3Vectors::VectorBucket", {"VectorBucketName": "crewops-dev-vectors"}
    )
    indexes = t.find_resources("AWS::S3Vectors::Index")
    props = sorted(
        (r["Properties"]["IndexName"], r["Properties"]["Dimension"]) for r in indexes.values()
    )
    assert props == [("intents", 512), ("rules", 512), ("scenarios", 512)]
    for r in indexes.values():
        p = r["Properties"]
        assert p["DistanceMetric"] == "cosine" and p["DataType"] == "float32"
        assert p["MetadataConfiguration"] == {"NonFilterableMetadataKeys": ["text"]}
        assert r["DependsOn"]  # created after the bucket
