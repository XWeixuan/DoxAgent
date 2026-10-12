# INTC Entity Map — 远端已发布快照

- Run ID：`init-intc-738849dbdbb04ae1a39efa0aec69c071-d1`
- 研究截止：`2026-09-22T04:23:27.309793+08:00`
- 发布：`2026-09-22T05:40:24.747063+08:00`
- 关系数：23；当前激活 revision 的 D1 指针已核对匹配。
- 以下是原始五字段的可读投影，保留原文和引用；未重新研究或更新关系事实。

| 关系主体 | 关系对象 | 关系类型 | 关系说明 | 关联业务或产品 |
| --- | --- | --- | --- | --- |
| Intel | Client Computing and Physical AI Group（CCPG，前身CCG） | 业务分部 | 负责PC与边缘计算平台及处理器，覆盖Core/Core Ultra等产品，直接客户包括经销商和OEM。【cite:O2】 | PC、边缘计算、Core/Core Ultra、客户端平台 |
| Intel | Data Center and AI（DCAI） | 业务分部 | 面向云、企业、电信和高性能计算客户提供Xeon处理器、AI加速器、网络控制器、IPU及定制ASIC。【cite:O2】【cite:O3】 | Xeon、AI加速器、IPU、网络产品、定制ASIC |
| Intel | Intel Foundry | 业务分部与制造平台 | 提供工艺开发、晶圆制造、先进封装、组装测试和设计使能，服务内部产品并拓展外部客户。【cite:O4】【cite:O5】 | 18A、14A、先进封装、晶圆制造、设计使能 |
| Intel | OEM、ODM、云服务商及分销渠道 | 客户与销售渠道 | Intel通过经销商、OEM/ODM和云服务商将客户端、服务器及AI产品导入终端设备、数据中心和边缘场景。【cite:O2】【cite:O6】【cite:O7】 | PC平台、Xeon平台、云基础设施、边缘计算 |
| Intel Foundry | ASML | 设备与技术合作 | 双方围绕High-NA EUV在量产中的应用及后续掩模尺寸转换持续协作。【cite:O8】 | High-NA EUV、先进制程光刻 |
| Intel | AMD、Qualcomm、NVIDIA、ARM/RISC-V生态及云厂商自研芯片 | 竞争关系 | Intel在客户端、数据中心、AI加速及处理器架构等市场面对上述厂商或替代架构竞争。【cite:O9】 | x86处理器、AI加速器、客户端芯片、服务器芯片 |
| Intel | NVIDIA | 联合开发与产品合作 | 双方计划开发多代数据中心和PC定制产品，包括采用NVLink的定制x86 CPU及x86 RTX SoC方向。【cite:O10】【cite:O11】 | 定制x86 CPU、NVLink、RTX SoC、AI基础设施 |
| Intel | Google | 云客户与联合开发 | 双方推进多代Xeon在Google Cloud中的部署，并共同开发多代定制IPU。【cite:O6】 | Xeon、Google Cloud C4/N4、定制IPU |
| Intel | Amazon Web Services（AWS） | 云客户与定制芯片合作 | 合作框架涵盖在Intel 18A上生产AI fabric chip、在Intel 3上生产定制Xeon 6，并探索18AP与14A。【cite:O7】 | Intel 18A、18AP、14A、Intel 3、AI fabric chip、Xeon 6 |
| Intel | SambaNova | AI系统合作 | 双方规划多年期Xeon与SambaNova推理加速方案合作，面向AI推理基础设施。【cite:O12】 | Xeon、AI推理、SambaNova RDU |
| Intel | Foxconn | 系统集成合作 | 围绕机架级AI基础设施推进系统集成，并探索CPU密集型方案。【cite:O13】 | 机架级AI、服务器系统、CPU密集型基础设施 |
| Intel | Fortinet | 芯片与制造合作 | 双方合作开发Fortinet Security Processor 6，结合Fortinet的ASIC能力与Intel的设计、封装和制造能力。【cite:O14】 | Fortinet Security Processor 6、网络安全芯片、先进封装 |
| Intel Foundry | Synopsys、Cadence、Siemens EDA、PDF Solutions | EDA与设计生态合作 | 这些合作伙伴为Intel Foundry客户提供EDA、IP、验证或制造数据分析支持，构成外部客户导入生态。【cite:O5】 | EDA工具、IP、设计使能、制造分析 |
| Intel Foundry | Amkor | 先进封装合作 | Intel Foundry与Amkor存在先进封装合作安排，用于扩展外部客户的封装与制造选择。【cite:O5】 | 先进封装、组装测试、外部代工 |
| Intel Foundry | United Microelectronics Corporation（UMC） | 制程技术合作 | 双方推进12nm及其衍生节点的技术合作，扩大Intel Foundry的制程与客户覆盖范围。【cite:O5】 | 12nm、衍生制程、晶圆代工 |
| Intel | 美国国防部、美国政府及国防工业基地（DIB）客户 | 政府项目与安全制造合作 | RAMP-C项目完成后，Intel与美国国防体系推动DIB客户利用Secure Enclave开展安全的高量制造。【cite:O15】【cite:O16】 | RAMP-C、Secure Enclave、安全制造、国防供应链 |
| Intel | SpaceX、xAI、Tesla | 战略制造合作 | Intel在2026年第一季度业绩电话会材料中披露加入Terafab战略合作，面向硅片工艺与制造协同。【cite:O3】 | Terafab、先进制程、晶圆制造 |
| Intel Foundry | Intel 18A、18A-P及14A制程路线 | 制程路线与客户导入 | 18A已进入内部产品高量生产，18A-P进入风险生产，14A处于开发及潜在外部客户设计承诺阶段，三者分别对应不同的制造和客户资格阶段。【cite:O24】【cite:O92】【cite:O3】 | 18A、18A-P、14A、PDK、晶圆制造 |
| Intel | Intel Core Series 3（Panther Lake） | 产品平台 | Core Series 3基于Intel 18A，覆盖消费、商用和边缘设备，并通过OEM合作伙伴推进系统上市。【cite:O136】 | Core Series 3、18A、AI PC、边缘计算 |
| Intel | Acer、ASUS、Dell、HP、Lenovo、Samsung等OEM伙伴 | 产品上市合作 | 这些OEM伙伴被列为Core Series 3系统的上市或后续上市合作方，覆盖消费、商用及边缘设备。【cite:O136】 | Core Series 3系统、AI PC、边缘设备 |
| Intel Foundry | Foveros-B、Foveros-R、Foveros Direct及EMIB-T | 先进封装技术平台 | Intel持续开发EMIB-T与Foveros系列；Foveros-B/R面向2027年高量制造，Foveros Direct混合键合支持计划用于18A-PT的2028年路线。【cite:O92】 | 2.5D/3D封装、EMIB-T、Foveros、AI与数据中心封装 |
| Intel Foundry | Lens Technology | 材料与先进封装合作 | 双方探索玻璃基板封装、精密玻璃加工和相关制造流程，以支持AI、数据中心、机器人及边缘计算平台。【cite:O83】 | 玻璃基板、先进封装、AI基础设施、边缘计算 |
| Intel | Altera | 股权与分部口径关系 | Intel于2025-09-12出售Altera 51%股权并停止合并；Altera之后的客户口径变化会影响Intel Foundry外部收入的解读。【cite:O24】 | FPGA、Intel Foundry外部收入、股权法投资 |

## C4 enrichment 的正式引用索引

别名作用域为本次 enrichment attempt；不能用 Global Research 汇总文档的 citation manifest 解读这些 O 编号。`resolved=true` 表示平台找到了对应 Observation，不代表本次已独立复核事实。

- `O2` → [text paragraphs 1](https://www.intc.com/news-events/press-releases/detail/1776/intel-reports-second-quarter-2026-financial-results)；resolved=`true`。
- `O3` → [text paragraphs 1](https://download.intel.com/newsroom/2026/earnings/1Q2026-Earnings-Call.pdf)；resolved=`true`。
- `O4` → [text paragraphs 1](https://www.intel.com/content/www/us/en/newsroom/news/corporate/intel-foundry-details-process-milestones-future-innovation-at-vlsi-symposium.html)；resolved=`true`。
- `O5` → [text paragraphs 1](https://www.intel.com/content/www/us/en/newsroom/news/corporate/intel-foundry-gathers-customers-partners-outlines-priorities.html)；resolved=`true`。
- `O6` → [text](https://www.intel.com/content/www/us/en/newsroom/news/artificial-intelligence/intel-google-deepen-collaboration-to-advance-ai-infrastructure.html)；resolved=`true`。
- `O7` → [text paragraphs 1](https://www.intel.com/content/www/us/en/newsroom/news/intel-strategic-collaboration.html)；resolved=`true`。
- `O8` → [text paragraphs 1](https://www.intc.com/news-events/press-releases/detail/1781/intel-foundry-and-asml-collaborate-to-accelerate-industry)；resolved=`true`。
- `O9` → [section_previews 2](https://www.sec.gov/Archives/edgar/data/50863/000005086326000011/intc-20251227.htm)；resolved=`true`。
- `O10` → [text paragraphs 1](https://www.intc.com/news-events/press-releases/detail/1750/nvidia-and-intel-to-develop-ai-infrastructure-and-personal)；resolved=`true`。
- `O11` → [text paragraphs 1](https://www.intel.com/content/www/us/en/newsroom/news/artificial-intelligence/intel-and-nvidia-to-jointly-develop-ai-infrastructure-and-personal-computing-products.html)；resolved=`true`。
- `O12` → [text](https://www.intel.com/content/www/us/en/newsroom/home.html)；resolved=`true`。
- `O13` → [text paragraphs 1](https://www.intel.com/content/www/us/en/newsroom/news/artificial-intelligence/intel-announces-new-ai-innovations-at-computex.html)；resolved=`true`。
- `O14` → [text paragraphs 1](https://www.intc.com/news-events/press-releases/detail/1775/intel-and-fortinet-collaborate-to-advance-cybersecurity)；resolved=`true`。
- `O15` → [text](https://www.intel.com/content/www/us/en/newsroom/news/intel-foundry/intel-completes-ramp-c-program-accelerating-momentum-for-secure-enclave.html)；resolved=`true`。
- `O16` → [section_previews 1](https://www.sec.gov/Archives/edgar/data/50863/000005086326000011/intc-20251227.htm)；resolved=`true`。
- `O24` → [sections 4](https://www.sec.gov/Archives/edgar/data/50863/000005086326000157/intc-20260627.htm)；resolved=`true`。
- `O92` → [section_previews 1](https://www.sec.gov/Archives/edgar/data/50863/000005086326000011/intc-20251227.htm)；resolved=`true`。
- `O136` → [text](https://www.intel.com/content/www/us/en/newsroom/news/client-computing/intel-launches-intel-core-series-3-processors-changing-the-game-for-everyday-computing.html)；resolved=`true`。
- `O83` → [text](https://www.intel.com/content/www/us/en/newsroom/news/corporate/intel-and-lens-technology-collaborate-to-enable-advanced-semiconductor-packaging-for-the-ai-era.html)；resolved=`true`。
