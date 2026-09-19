import asyncio
from agents import Agent, Runner, ModelSettings, set_default_openai_key
from agents.decorators import tool
from dotenv import load_dotenv
import os

load_dotenv()

set_default_openai_key(os.getenv("OPENAI_API_KEY", ""))

REASONING_HIGH = ModelSettings(
    reasoning={
        "effort": "high"
    }
)


async def main():
    extrator_agent = Agent(
        name="document_extractor",
        model="gpt-5.6-luna",
        model_settings=REASONING_HIGH
        # instructions=""
    )

    # Human in the loop here:
    # - the goal of this process would be to come up with an interactive process to discuss with the user
    #   how the outline of the slides should be planned, the user should fine-tune it before the agent 
    #   starts to generate the slides
    planner_agent = Agent(
        name="slide_planner",
        model="gpt-5.6-sol",
        model_settings=REASONING_HIGH
    )

    author_agent = Agent(
        name="slide_author",
        model="gpt-5.6-terra",
        model_settings=REASONING_HIGH
        # instructions=""
    )
    revisor_agent = Agent(
        name="slide_revisor",
        model="gpt-5.6-sol",
        model_settings=REASONING_HIGH
        # instructions=""
    )
    result = await Runner.run(author_agent, "It's chill, what's up.")
    print(result.final_output)

if __name__ == "__main__":
    asyncio.run(main())