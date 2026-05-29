# Contents of /ai-agent/ai-agent/src/tools/registry.py

class Registry:
    def __init__(self):
        self._agents = {}

    def register(self, name, agent):
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered.")
        self._agents[name] = agent

    def get_agent(self, name):
        agent = self._agents.get(name)
        if agent is None:
            raise ValueError(f"Agent '{name}' not found.")
        return agent

    def list_agents(self):
        return list(self._agents.keys())