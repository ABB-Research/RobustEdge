"""
    Attack factory that creates attack objects.

    Author: Eshaan Mudgal
"""
from attack_agents.influxdb_burst_attack import InfluxDBBurstAttack

# Registry of available attack types.
# To add a new attack:
#   1. Create a class in attack_agents/ that inherits from AttackAgent and implements
#      startAttack() and stopAttack().
#   2. Add an entry here: "YourAttackName": YourAttackClass
_ATTACK_REGISTRY = {
    "InfluxDBBurstAttack": InfluxDBBurstAttack,
}


def AttackFactory(attackType):
    """Return a new instance of the named attack agent.

    Args:
        attackType: Key from the attack registry (e.g. "InfluxDBBurstAttack").

    Raises:
        ValueError: If attackType is not registered.
    """
    if attackType not in _ATTACK_REGISTRY:
        raise ValueError(f"Unsupported attack type '{attackType}'. Known types: {list(_ATTACK_REGISTRY)}")
    return _ATTACK_REGISTRY[attackType]()