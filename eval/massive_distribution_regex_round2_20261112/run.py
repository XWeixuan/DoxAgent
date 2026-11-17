"""Four manually authored simple recipes; one frozen attempt each, no autotuning."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIOR = ROOT / 'eval/massive_distribution_regex_tuning_20261112'
sys.path.insert(0, str(PRIOR))
import ops

ops.HERE = HERE  # Reuse the native replay writer, with a separate output directory.
TICKERS = ['BE', 'COHR', 'NBIS', 'CEG']


def rx(pattern, field):
    return {'regex': pattern, 'field': field}


def subject(name, pattern):
    # A clear subject can occur in either headline or publisher summary.
    return {'id': name, 'any': [rx(pattern, 'title'), rx(pattern, 'summary')]}


def body(name, pattern):
    return {'id': name, 'any': [rx(pattern, 'body')]}


DC_PROJECT = r'\bdata[ -]?cent(?:er|re)s? (?:campus|capacity|construction|expansion|development|pipeline)\b'
COMPUTE_SUPPLY = r'\b(?:AI compute capacity|GPU clusters?|GPU deployment|bare metal servers?)\b'

RECIPES = {
    'BE': [
        subject('company', r'\b(?:Bloom Energy|FuelCell Energy|FCEL)\b'),
        subject('stationary_power_market', r'\b(?:stationary fuel cells?|solid oxide|SOFC|distributed (?:generation|power|energy)|microgrids?|electroly[sz]ers?|green hydrogen|renewable power)\b'),
        subject('storage_and_grid_market', r'\b(?:battery (?:energy )?storage|energy storage systems?|grid (?:balancing|edge|modernization)|DERMS)\b'),
        subject('alternative_generation', r'\b(?:nuclear power|nuclear fusion|nuclear decommissioning|NANO Nuclear|SMRs?)\b'),
        body('stationary_power_fact', r'\b(?:(?:on[ -]site|distributed) (?:natural gas )?(?:power|generation)|power generation contracts?|virtual power plants?|electroly[sz]ers?|stationary fuel cells?)\b'),
        subject('compute_facility', DC_PROJECT),
    ],
    'COHR': [
        subject('company', r'\b(?:Coherent Corp(?:oration)?|COHR|MACOM)\b'),
        subject('optical_product_market', r'\b(?:photonic computing|silicon photonics|optical (?:fiber|fibre|cables?|interconnects?|transceivers?)|data center interconnect|fiber connectivity|industrial lasers?|SiC substrates?)\b'),
        body('specific_photonic_fact', r'\b(?:silicon photonics|photonic (?:computing|integrated circuits?|quantum)|optical interconnects?|co-packaged optics|fiber connectivity|specialist lasers?|wavelength (?:services|revenue))\b'),
        subject('compute_facility', DC_PROJECT),
        subject('compute_supply', COMPUTE_SUPPLY),
    ],
    'NBIS': [
        subject('cloud_provider', r'\b(?:Nebius|CoreWeave|Runpod|Together AI|Rackspace)\b'),
        subject('compute_service_market', r'\b(?:GPU (?:cloud|compute|computing)|AI cloud|bare metal|AI data[ -]?cent(?:er|re)s?|data center services|KV cache)\b'),
        subject('compute_facility', DC_PROJECT),
        subject('facility_dependencies', r'\bdata center (?:power|cooling|optical fiber|electrical equipment)\b'),
        body('compute_service_fact', r'\b(?:GPU cloud|GPU deployment|GPU-accelerated compute|AI cloud revenue|AI cloud infrastructure|hyperscale computing infrastructure|data cent(?:er|re) joint venture|Kubernetes[ -]as[ -]a[ -]service)\b'),
    ],
    'CEG': [
        subject('company', r'\b(?:Constellation Energy|Calpine)\b'),
        subject('nuclear_market', r'\b(?:nuclear (?:power|energy|reactors?|engineering|fuel|waste|fusion|decommissioning|grade|phase)|spent (?:nuclear )?fuel|uranium|SMRs?)\b'),
        subject('electricity_market', r'\b(?:power generation|power quality|renewable power|energy storage|battery storage|electric(?:ity)? grid|grid (?:modernization|balancing)|HVDC|net-zero energy buildings)\b'),
        body('operating_market_fact', r'\b(?:electricity prices?|power prices?|solar PV projects?|utility-scale (?:solar|energy storage)|battery energy storage systems?)\b'),
    ],
}

LESSONS = {
    'BE': '本轮先用可直接解释的业务对象搭配确定范围，不用容量数字和泛动作词推断行业联系。替代市场是否纳入应由业务尺度决定，不能为迎合个别标签而写隐蔽排除条件；对未覆盖的信息接受漏召并明确报告。简单词组也可能出现在公司背景或用途名单中，简化不等于消除了误召。',
    'COHR': '把公司主体、产品市场和少量高辨识度正文事实分别表达，可以降低维护和解释成本。撤掉宽泛项目推断会丢失一些间接相关消息，但不应立刻用数值、单位、机构清单和文章专用搭配补回来；是否接受这种取舍应看业务合理性，而不是开发集是否恰好过线。',
    'NBIS': '优先写清服务产品及必要设施条件，避免让泛行业词独立放行。背景介绍仍可能包含真实服务词，标题/摘要优先也可能漏掉正文经营事实。承认这两种边界，比继续叠加供应商名单和多层条件更可维护。',
    'CEG': '完整业务范围可以先由少量稳定市场词表达，不必给每个产品补不同的事件谓词。运行价格、供给设施等明确正文事实可独立补召回；一般背景应用仍需在偏差中暴露，而不以越来越长的排除清单追分。',
}


def make():
    for ticker, groups in RECIPES.items():
        target = HERE / ticker / 'regex/attempt-01/candidate.json'
        assert not target.exists(), 'Do not overwrite a scored/frozen recipe'
        config = ops.read(ops.DATA / 'baseline-terms' / f'{ticker}.json')
        config['l2'] = {'en': {'groups': groups}}
        ops.write(target, config)
    ops.write(HERE / 'freeze.json', {
        'frozen_at': ops.now(), 'candidate_sha256': {t: ops.sha(HERE/t/'regex/attempt-01/candidate.json') for t in TICKERS},
        'basis': 'development data and plain business scope; one new recipe per ticker',
        'prior_validation_already_exposed': True, 'validation_is_diagnostic_only': True,
        'no_post_score_candidate_edits': True,
    })


def score(split):
    frozen = ops.read(HERE / 'freeze.json')
    for ticker in TICKERS:
        assert ops.sha(HERE/ticker/'regex/attempt-01/candidate.json') == frozen['candidate_sha256'][ticker]
        result = ops.evaluate(ticker, 'attempt-01', split)
        ops.note(HERE/ticker, ticker, 'regex', f'MASSIVE-{ticker}-20261112-02', LESSONS[ticker], with_validation=split == 'validation')
        print(ticker, split, result)


if __name__ == '__main__':
    if sys.argv[1] == 'make': make()
    else: score(sys.argv[1])
