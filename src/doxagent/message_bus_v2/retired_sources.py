"""Explicitly retired source identities; successors retain the merged feeds."""

REPLACEMENTS = {
    "crn_cloud": "crn_cloud_security_datacenter",
    "crn_data_center": "crn_cloud_security_datacenter",
    "crn_security": "crn_cloud_security_datacenter",
    "quantum_insider_business": "quantum_insider",
    "quantum_insider_capital_markets": "quantum_insider",
    "quantum_insider_national": "quantum_insider",
    "register_cloud": "register_software_cloud_security",
    "register_security": "register_software_cloud_security",
    "register_software": "register_software_cloud_security",
    "siliconangle_big_data": "siliconangle_software",
    "siliconangle_cloud": "siliconangle_software",
    "siliconangle_security": "siliconangle_software",
    "technews_ai": "technews_hardware",
    "technews_semiconductor": "technews_hardware",
    "venturebeat_data": "venturebeat_software",
    "venturebeat_orchestration": "venturebeat_software",
    "venturebeat_security": "venturebeat_software",
    "wccftech_ai": "wccftech_hardware",
    "wccftech_industry": "wccftech_hardware",
    "globenewswire_search": None,  # Explicit removal; Massive is still a separate test source.
}
