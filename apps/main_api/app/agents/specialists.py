from app.agents.mock_tools import (
    MOCK_ANALYST_TOOLS,
    MOCK_DOCTOR_TOOLS,
    MOCK_FARM_TOOLS,
    MOCK_INFORMATION_TOOLS,
    MOCK_SECRETARY_TOOLS,
)
from app.llms import llm_especialista, llm_rapido
from app.prompts.router import ROUTER_SYSTEM_PROMPT
from app.prompts.specialists import (
    ANALYST_SYSTEM_PROMPT,
    DOCTOR_SYSTEM_PROMPT,
    FARM_SYSTEM_PROMPT,
    INFORMATION_SYSTEM_PROMPT,
    SECRETARY_SYSTEM_PROMPT,
)
from langchain.agents import create_agent

router_app = create_agent(
    model=llm_rapido,
    tools=[],
    system_prompt=ROUTER_SYSTEM_PROMPT,
)

farm_app = create_agent(
    model=llm_especialista,
    tools=MOCK_FARM_TOOLS,
    system_prompt=FARM_SYSTEM_PROMPT,
)

doctor_app = create_agent(
    model=llm_especialista,
    tools=MOCK_DOCTOR_TOOLS,
    system_prompt=DOCTOR_SYSTEM_PROMPT,
)

analyst_app = create_agent(
    model=llm_especialista,
    tools=MOCK_ANALYST_TOOLS,
    system_prompt=ANALYST_SYSTEM_PROMPT,
)

secretary_app = create_agent(
    model=llm_especialista,
    tools=MOCK_SECRETARY_TOOLS,
    system_prompt=SECRETARY_SYSTEM_PROMPT,
)

information_app = create_agent(
    model=llm_especialista,
    tools=MOCK_INFORMATION_TOOLS,
    system_prompt=INFORMATION_SYSTEM_PROMPT,
)
