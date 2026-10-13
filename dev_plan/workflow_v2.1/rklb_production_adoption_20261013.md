# RKLB production research adoption — 2026-10-13

用户授权替换生产RKLB D1/D2/D3，保留现有Event Library。通过生产现有research_adoption模块选择性导入、正式ACTIVATE/CAS激活，没有替换整库或重跑研究。

- D1：rklb-v21-20261012-01-d1。
- D2：rklb-v21-20261012-01-d2，原5 shell，PARTIAL。
- D3：adopt-v21-rklb-20261013-b30ac7c1ddf4-d3，生产分配v13，138 policies；源为测试v12，source SHA256 b30ac7c1ddf4b4e9017d771bc250d3bd1ffc3f0fc635e8d97ae4025877851a4b。逐项对比policies及document2_ref与源完全相同，保留PARTIAL与原缺口，没有policy修补。
- 激活revision：init-rklb-dffa67ede084412b83b0897850f8c89e-activation。research_matches=true，bus/runtime ACK均true。
- Event Library保留生产原v25、原root，两个实际文件SHA256前后完全相同；未使用源测试库的v1覆盖生产。
- monitoring_configuration前后完全相同。

证据：eval/rklb_production_adoption_20261013/{manifest,before,after}.json。远端回退引用快照：/data/production-backups/rklb-adoption-20261013/before.json；导入回执：/data/research/adoptions/adopt-v21-rklb-20261013-b30ac7c1ddf4-d3/adoption-receipt.json。回退应正式激活旧文档引用，并继续保留当时最新Event/监测配置，不能覆盖业务整库。

本轮没有代码修改、镜像重建、生产服务重启、CDECR/O2/O4研究运行；只执行已授权文档迁入与正式激活。
