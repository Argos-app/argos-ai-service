"""Porta da consulta ao catálogo relacional global de medicamentos."""

from typing import Protocol

from argos_mcp_data.domain.cattle import Page
from argos_mcp_data.domain.medicine import ListMedicinesRequest, MedicineCatalogRecord


class MedicineReadRepository(Protocol):
    async def list_medicines(
        self, request: ListMedicinesRequest
    ) -> Page[MedicineCatalogRecord]: ...
