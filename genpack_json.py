import json,logging

_genpack_json = None

def load():
    global _genpack_json
    if _genpack_json is None:
        _genpack_json = json.load(open("./genpack.json"))
        logging.debug("genpack.json loaded")
    return _genpack_json

def get(name, default = None):
    return load()[name] if name in load() else default