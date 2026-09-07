from pydantic import BaseModel


def update_model_from_schema(model: object, schema: BaseModel) -> None:
    for field in schema.model_fields_set:
        setattr(model, field, getattr(schema, field))
