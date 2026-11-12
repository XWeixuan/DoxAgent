"""Massive-only rule selection, sharing the bounded production matching engine."""
from .relevance import match_rules


def massive_configured(terms, language):
    return bool(terms and any(terms.massive_l2.get(k) and terms.massive_l2[k].groups for k in (language, "en")))


def massive_relevant(terms, language, message):
    return match_rules(terms.massive_l2, language, message)


def uses_massive_rules(source):
    from .schema import AcquisitionMode
    return source.acquisition_mode is AcquisitionMode.MASSIVE_DISTRIBUTION and (
        source.delivery_mode == "production" or
        source.default_parameters.get("massive_regex_enabled") is True
    )
