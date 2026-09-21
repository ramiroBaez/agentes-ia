"""Function calling: give the model ONE real tool.

Demonstrates the core tool-use loop behind every agent:

    model requests the tool -> our code executes it -> model composes the final answer

The model never executes code. It only decides and asks; our code runs the real
function and feeds the result back. Repeating this loop with more tools is
exactly what agent frameworks automate under the hood.
"""

import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Simulated dataset. In production this would be a call to a real weather API.
# The model never sees this: it only sees the tool's contract (declaration).
WEATHER = {
    "la plata": "18°C, cloudy",
    "buenos aires": "20°C, sunny",
    "córdoba": "22°C, partly cloudy",
    "mendoza": "19°C, rainy",
}


def get_weather(city: str) -> str:
    """Simulated weather lookup for a city."""
    return WEATHER.get(city.lower(), f"No data available for {city}.")


def build_weather_tool() -> types.Tool:
    """Describe the tool to the model (step 1 of the cycle)."""
    declaration = types.FunctionDeclaration(
        name="get_weather",
        description="Get the current weather for a city.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Name of the city to query the weather for.",
                },
            },
            "required": ["city"],
        },
    )
    return types.Tool(function_declarations=[declaration])


def main() -> None:
    if not API_KEY or API_KEY == "your_gemini_api_key_here":
        print("Error: set GEMINI_API_KEY in your .env file to call the API.")
        raise SystemExit(1)

    client = genai.Client(api_key=API_KEY)
    config = types.GenerateContentConfig(tools=[build_weather_tool()])

    question = "What is the weather in La Plata?"

    # Steps 2 and 3: send the message; the model either requests the tool
    # (function_calls) or answers directly (text).
    response = client.models.generate_content(
        model=MODEL,
        config=config,
        contents=question,
    )

    if response.function_calls:
        call = response.function_calls[0]
        print(f"Model requested tool: {call.name} with {call.args}")

        # Steps 4 and 5: our code executes the real function.
        result = get_weather(**call.args)
        print(f"Code executed get_weather() and returned: {result}")

        # Step 6: feed the real result back to the model so it can compose
        # the final natural-language answer.
        conversation = [
            types.Content(role="user", parts=[types.Part(text=question)]),
            types.Content(role="model", parts=[types.Part(function_call=call)]),
            types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name=call.name, response={"result": result}
                    )
                ],
            ),
        ]

        final_response = client.models.generate_content(
            model=MODEL,
            config=config,
            contents=conversation,
        )

        print("\nFinal answer from the model:")
        print(final_response.text)
    else:
        print("The model answered directly without using the tool:")
        print(response.text)


if __name__ == "__main__":
    main()