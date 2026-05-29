# Contents of /ai-agent/ai-agent/src/memory/store.py

class MemoryStore:
    def __init__(self):
        self.memory = {}

    def save(self, key, value):
        self.memory[key] = value

    def retrieve(self, key):
        return self.memory.get(key, None)

    def clear(self):
        self.memory.clear()