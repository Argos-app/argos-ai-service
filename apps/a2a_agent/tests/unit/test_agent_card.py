"""Contrato local do card A2A publicado pelo serviço documental."""

from a2a.types import a2a_pb2
from argos_a2a_agent.adapters.inbound.a2a_server import create_agent_card


def test_agent_card_builds_protobuf_security_requirement():
    card = create_agent_card("https://argos.example/a2a")

    assert card.security_requirements
    requirement = card.security_requirements[0]
    assert isinstance(requirement, a2a_pb2.SecurityRequirement)
    assert requirement.schemes["serviceBearer"].list == []
