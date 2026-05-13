# Changelog

## [1.2.0](https://github.com/GoogleCloudPlatform/cxas-scrapi/compare/v1.1.0...v1.2.0) (2026-05-13)


### Features

* Add Slot Filling DAG Framework documentation and Bella Notte ex… ([e8d9fa2](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e8d9fa2a82ca11bb7bf2c19011d71252385a1b0f))
* Add Slot Filling DAG Framework documentation and Bella Notte example ([b91d048](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b91d04872cdf8792add9bb72b6917426e1c5d119))
* bella-notte: replace with full CXAS app structure ([055240b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/055240bd72802a8075facf4e2ff76b2f1226ee3b))
* **bella-notte:** auto-confirm, stall gating, fresh-pending setter h… ([8a021be](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/8a021be307dfc3ecea7e967e017c705361db8219))
* **bella-notte:** auto-confirm, stall gating, fresh-pending setter hiding ([b0ddd35](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b0ddd359ba106ed31cddff8c787ccad6971de886))
* **cli:** add directory support, async execution, runs flag, and modality support to evals report ([819cb29](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/819cb291612f54b2755d3cf07b9602845a6f813c))
* **cli:** add directory support, async execution, runs flag, and modality support to evals report ([36f07be](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/36f07bec8a26f292e0fddec3b505273bbed94b41))
* **cli:** add directory support, filtering, and robust expectation check to evals report ([9f39cfc](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/9f39cfcc3b709c910b21107b88f16412e6e44b22))
* **cli:** add directory support, filtering, and robust expectation check to evals report ([6e247e9](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6e247e900afaf810e08baa2f5e8bd082b625217a))
* comprehensive DFCX to CXAS migration optimization, Stage 1 and 2, and minor enhancements ([#93](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/93)) ([625f464](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/625f464aade7b0ed84594f98a248f07bfce2dc99))
* Fix fresh_pending to detect new slots added mid-readback ([3a5c17f](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3a5c17feaaf2bb6e7e3a1c749387d69a06e100a4))
* implement GCS storage support for combined evaluation reports and add CLI flag ([bf85a6c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/bf85a6c9906880e4a63176e7958a590bd18a3a18))
* implement GCS storage support for combined evaluation reports and add CLI flag ([c17b9a8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c17b9a83acbf422c24bd8dea85aad0c7dc76d077))
* integrate migration into Scrapi CLI ([#59](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/59)) ([ee6320c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ee6320c71e859b93a26c49eec182365e2ce29373))
* update agent-foundry skill to use subagents to improve context management ([a9a1227](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a9a12272c9d79bb20103e714673fcc2daada2709))


### Bug Fixes

* add missing google-cloud-storage dependency ([7bef179](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7bef17958e72502e57f1b53d10a84d0cbf38d139))
* Add missing google-cloud-storage dependency ([12ba121](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/12ba1211363f2f7df979ae06daa0c63a471defeb))
* add noqa comments for ruff lint (F821, I001) ([747787d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/747787d3d942673a4f62cc1c1bec845b6007dbc8))
* **C002:** shorten _get_args docstring to satisfy line length linter ([a095f2e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a095f2e476ccecd47baf718291fd81e5cf177c47))
* **C002:** use AST to count callback args instead of regex splitting ([70d1e5d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/70d1e5d35c2548f8c530787609267611bec8b482))
* init-github-action nested app dirs ([0710e2e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/0710e2e551249843ec28ae165c5eb3a1c7d65734))
* **linter:** remove app.json tools check and update tests ([a516fa6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a516fa652b3d630c11d8fcfa9f66673da3d79df1))
* **linter:** remove app.json tools check and update tests ([a3abc4e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a3abc4e7b984b4ffab3819832b1110ed996edf98))


### Documentation

* add Design Guide, Patterns, and Tutorials sections ([c836509](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c836509fda7a65433043f18b5bf616149b128043))
* **agent-development:** add team collaboration guide for multi-developer Git workflow ([eabedd0](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/eabedd07f1d1a4d7594e831822386f6e23122bc5))
* **agent-development:** add team collaboration guide for multi-developer Git workflow ([cd3c520](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/cd3c520b5f06d529c0c16feddeab7617cce11b39)), closes [#90](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/90)
* Docs/design guide patterns tutorials ([c40c375](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c40c37539d3e278412cab8f0f082dd40d86a30ab))
* scope DAG slot manager state to prevent cross-DAG-agent contamination ([4e5b0b0](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4e5b0b0a322034065e038732627cf986145644c0))
* scope DAG slot manager state to prevent cross-DAG-agent contamination ([7749ded](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7749ded6b08798840b30d9e6c638d8e81288796d))
* **skills:** add multilingual agent patterns to cxas-agent-foundry s… ([23b70e4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/23b70e48326482c598ba7a5f1d46ca35e2e0d4cf))
* **skills:** add multilingual agent patterns to cxas-agent-foundry skill ([e827656](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e8276567d2811bdd50d1e7157abaedd35cc7092a))
* **skills:** add speech rate and pacing guidance to cxas-agent-found… ([4a85484](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4a8548437280e5030be39f2cdc3510fcd52e625c))
* **skills:** add speech rate and pacing guidance to cxas-agent-foundry skill ([90835bc](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/90835bc750a971d10ab749424d49c369129e33c8))

## [1.1.0](https://github.com/GoogleCloudPlatform/cxas-scrapi/compare/v1.0.0...v1.1.0) (2026-05-05)


### Features

* Add --modality flag to cxas_sim_eval run_evals script ([77bab91](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/77bab915551c88768059db7ba676bc86f698f870))
* Add --modality flag to cxas_sim_eval run_evals script ([4ea51ea](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4ea51ea55789ddd2e1e26d464cc4f12b0b496758))
* add GCS export and unified reporting path ([8bf2cd6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/8bf2cd65948aa6929188eb45950bc24b12c9135a))
* add GCS export and unified reporting path ([4670d77](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4670d77fec7ad11debc0f90a09e40214591de673))
* add progress bar for eval runs ([3960546](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3960546042bdb4cb71a7a37ca4c3451c1a85009a))
* add progress bar for eval runs ([f3d33d6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f3d33d643dcf07a3586ed237bf40cdf1f686e40b))
* Add run-session text CLI to start a test session for the app ([ae13a05](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ae13a057e1607fe868c43be63870bf5360e09092))
* Add run-session text CLI to start a test session for the app ([a6c36eb](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a6c36ebf38b52fc23710dba3c84ac51ba8199302))
* Add simulations to Golden conversion ([61469b3](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/61469b3b077fd7f764847d3b13bffcd29e708dc9))
* Add simulations to Golden conversion ([4645add](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4645add9b3d6cba0ece7fd59a4bc051a4017fbef))
* Add support for providing environment.json file when branching app ([3170a40](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3170a40aaba4c67f2f95712a4f046422fc9d81d8))
* complete refactor to Pydantic models, fix codebase porting bugs ([#26](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/26)) ([a38648c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a38648cc4cbd563cbdd5662d59edb2d261a2e35f))
* create deployment updates ([e7ba86c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e7ba86ce426bf0b4aab4cd26776ca24038c041de))
* **evals:** capture and render agent transfers and custom payloads i… ([f33b6b3](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f33b6b35dff436592da309e6a45d23929a2ce4d5))
* **evals:** capture and render agent transfers and custom payloads in sim reports ([46bf15e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/46bf15e62ab831f3879cb08de511a26db0ad429a))
* implement colab dashboard and CLI dashboard modules, and refactor visualization components ([2d6f80b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2d6f80b09844b54a1b16f944366aae73666cffe6))
* implement colab dashboard and CLI interface modules, and refactor visualization components ([b0bd6f5](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b0bd6f5dbf247ce08875d0bdbaf47aebbf01a47b))
* improve colab migration logs ([#44](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/44)) ([ff43d78](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ff43d78db08600eb32c34c3877cf2bacfa29f3ff))
* **linter:** add I014 rule to warn when current_date is missing from instructions ([68d6a3d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/68d6a3d0c0cd4b05760c6bf186fcfe2920a39e8c))
* **linter:** add I014 rule to warn when current_date is missing from… ([97a7e53](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/97a7e536782fa662ee1cf8d970e3feafe3487f2b))
* more verbose implementation for channel types and deployments ([db79776](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/db7977663bfaaa13bae589a5ca65a4a0791cd435))
* rich progress bar. remove aliev-progress ([737c84a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/737c84a50c2cdd9db9749c953ad7e206a1064af5))
* rich progress bar. remove aliev-progress ([5a61d7c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5a61d7cf2e68c42b5cfc30b4183ddc15af94570a))
* support events via audio ([d5adc0a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d5adc0a8151b49ebc0c0038a82e0541f31748048))
* support events via audio ([f016606](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f016606c257e69716db51b8c5d83f031cc992fb4))


### Bug Fixes

* (docs) changed underscores in terminal command params to hyphens ([5184d72](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5184d7296c66d63f1ca2dd68a162ac10f4a865f5))
* add missing alive-progress (Scrapi-wide fix) and nest_asyncio (migration specific) dependencies to setup.py ([57613b0](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/57613b01fbd8157dd46cd02a4e3160d8a78b0422))
* add missing alive-progress dependency to setup.py ([14357e1](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/14357e1a818f81580e097885694baf0959cd33c2))
* add missing voice config to app.json template in foundry skill ([3821b09](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3821b09b0c695cfcc292f3635ac41bdb44ed02f7))
* add missing voice config to app.json template in foundry skill ([4b7d1a6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4b7d1a6589be98752687bfba58cc767011289634))
* add rich progress bar, remove alive-progress ([7bc1113](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7bc111300fa06846b67d845913d4f54fa343ac9e))
* correct indentation for justification strings in TurnEvals class ([9bfb352](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/9bfb3527abef586c4f15fa640b0459fb4c0056fc))
* Fix bugs in init-github-action ([a94c019](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a94c019a0c96196d09b6a245bac7b9f5ac1ed240))
* Fix bugs in init-github-action ([3d498bc](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3d498bcdab66e35f7c091751d2f70e41d5c5ecdc))
* ignore localhost in readme link check ([699eb66](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/699eb663b426013f7c01f5938d35a112aee7c370))
* **linter:** accept display names in S004 child agent references ([0ca0727](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/0ca0727a155881a3021cac50e94bf7ca7f140376))
* **linter:** accept display names in S004 child agent references ([2313da9](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2313da95c1544fb37fa94c5955a80c2591b94848)), closes [#16](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/16)
* **linter:** C009 false-positives on dict[str, Any] annotations ([cd40176](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/cd40176240faeb7586277fd4e073ed4330f61ae1))
* linting ([50dc27e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/50dc27ee78daea9bab45305a22e746925830301a))
* linting ([7983803](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/79838037d283873961bc8cdcd6968c1996a33396))
* **lint:** update ruff version and fix import sorting ([c006eb0](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c006eb0dae67b6056ceaa5c0eb4aae5567879d7b))
* parse callback signatures via ast in C009 to handle dict[str, Any] ([6534cbe](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6534cbea05b87efe8172f2b568a6b056d90fca35)), closes [#56](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/56)
* rename app_id to app_name; remove unused code ([59804b8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/59804b88c337db2fe29432425d5d2ceac0965f2e))
* resolve deployment crashes, finalize e2e migration parity with colab tool (with enhancements) ([#37](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/37)) ([6a3ae12](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6a3ae12819d1ccb9843b4d056f233a48d77b95a9))
* revert app_id for create_app method ([bc41156](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/bc4115672f030b0a1647e3cf52a2318821554b4f))
* scope TOOL_INPUT/OUTPUT justification to failure  and update README ([54ce6cd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/54ce6cd5d0f214e94e218b35e23686ab554b44f7))
* typing, imports and removed deprecated version_id ([fd83967](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/fd839677a74e1ca5d0f0179338da98df023c1026))
* update app_id to app_name in CLI ([aa65fdb](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/aa65fdbc79763725cfddfed1bf548ec152977193))
* update app_id to app_name in CLI ([057f853](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/057f8533fd68cc09e6e14cc17743a92b2c8c0e71))
* update to use proper typing ([c895cd8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c895cd8cb7396e7593fec2290a84484520bdb350))
* use regex to parse for events ([e3c4d68](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e3c4d687d2e127c529e46d2cf2bc77e0f30a15cb))


### Documentation

* add Mercedes F1 fan agent PRD example ([42a7d64](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/42a7d642935bd5746b6e019c3450a19a4a18de88))
* add Mercedes F1 fan agent PRD example ([d35b056](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d35b0568ddf843085a1ded38c7410dc731c9f3f1))
* fix mkdocs build warnings ([b178ec2](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b178ec2b929b5d5704080ede562e2adcc72e2928))
* fix setup instructions across documentation ([4bd9cce](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4bd9cce322503f7f943598a2314e539fff839066))
* fix setup instructions across documentation ([1b2e47c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/1b2e47c31735508289f09da4e543161d67ef480a))

## 1.0.0 (2026-04-22)


### Features

* Init release of CXAS SCRAPI ([5f37dea](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5f37deacf2f8b7cb16793bf071ffa076c8a1db75))


### Miscellaneous Chores

* release 1.0.0 ([3b9b0c2](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3b9b0c2609f80646cbd312049569438f22b7290f))
