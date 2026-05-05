class OmniCoreAgentPromptBuilder:
    def __init__(self, system_suffix: str):
        self.system_suffix = system_suffix.strip()

    def build(self, *, system_instruction: str) -> str:
        if not system_instruction.strip():
            raise ValueError("System instruction is required.")

        return f"""<system_instruction>
{system_instruction.strip()}
</system_instruction>

{self.system_suffix}
""".strip()
