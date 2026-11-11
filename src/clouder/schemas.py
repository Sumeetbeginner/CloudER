from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9-]*$")]
Kind = Literal["compute", "bucket", "database", "loadbalancer"]


class ResourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cpu: Literal[1, 2] = 1
    memory_mb: Literal[256, 512, 1024] = 512
    targets: list[str] = Field(default_factory=list, max_length=10)


class CreateResource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    kind: Kind
    spec: ResourceSpec = Field(default_factory=ResourceSpec)

    @model_validator(mode="after")
    def validate_spec(self) -> "CreateResource":
        if self.kind == "loadbalancer" and not self.spec.targets:
            raise ValueError("A load balancer requires at least one compute target")
        if self.kind != "loadbalancer" and self.spec.targets:
            raise ValueError("Only load balancers accept targets")
        if len(set(self.spec.targets)) != len(self.spec.targets):
            raise ValueError("Targets must be unique")
        if self.kind == "database" and self.spec.memory_mb < 512:
            raise ValueError("PostgreSQL requires at least 512 MB in this service catalog")
        return self


class KeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    role: Literal["owner", "operator", "viewer"]


class SecretWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    value: str = Field(min_length=1, max_length=8192)
