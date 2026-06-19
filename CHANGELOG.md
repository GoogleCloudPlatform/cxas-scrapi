# Changelog

## [1.4.1](https://github.com/GoogleCloudPlatform/cxas-scrapi/compare/v1.4.0...v1.4.1) (2026-05-29)


### Bug Fixes

* fix .gitignore to prevent packaging issues ([952070e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/952070efb41613959807b3a513150ac4dfb35b40))

## [1.4.0](https://github.com/GoogleCloudPlatform/cxas-scrapi/compare/v1.3.0...v1.4.0) (2026-05-29)


### Features

* add audio replay capability to combined evaluation reports ([cf5b904](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/cf5b9049f408f075c8f45e3cb7ecc0a836652f57))
* add audio replay capability to combined evaluation reports ([8ac99ac](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/8ac99aced0b73c7b2041862cfcb6b0144c538ed5))
* add cxas-loss-analysis skill for conversation loss and patterns analysis ([b6f0a27](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b6f0a271607bc0080298d0a49e49635e96fbdabe))
* Add llm-based linting to capture semantic errors. ([6fab5a6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6fab5a6132a6fbab5963b577424cc48ca20e9daf))
* Add llm-based linting to capture semantic errors. ([34ab035](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/34ab0358731667817e22d266da3bc7624773574e))
* add native support for CES_API_ENDPOINT and CES_TRANSPORT ([0ecf7fa](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/0ecf7faa9fef543e58031ea54c1e853f39199822))
* add native support for CES_API_ENDPOINT and CES_TRANSPORT ([214f27a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/214f27abb239b43a61d70deab6868d98ee81d724))
* **cli:** add cli commands for list version and compare version ([603612b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/603612b69ebbda4394aedbce8986085824af8c46))
* **cli:** add cli commands for list versions and compare versions ([ba92521](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ba9252100938cd4c9e34e0549f31914fa008a2c9))
* **cli:** add GECX tools, callbacks, and variables list/delete subcommands ([4ef9760](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4ef9760b66c80869522bea15cf886f6ace141826))
* improve loss analysis pipeline with time filters, server-side filtering, and performance optimizations ([abecd37](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/abecd37df8f09708c5a1e6e8af614a8f5f25c2f3))
* unify migration interfaces, implement migration profiles, fixes ([#193](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/193)) ([0d57531](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/0d57531c4490dcb4cf95fd029f44e05595200611))


### Bug Fixes

* app_name propagation in cxas create ([d9b655a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d9b655a8d7ad4dac83e9c707eb0e4117a7061367))
* resolve lint errors and improve placeholder validation ([ab5b009](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ab5b009a9e15ef91c22acd58b54820384a3b11f0))
* **trace:** correct GECX/CES Console URL query parameter route in console_url ([9ace03c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/9ace03cc103718d869d12e58f8d06aca9821c81e))


### Documentation

* add GoogleSearchTool schema and create tool schema generation w… ([d9758cb](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d9758cb78c4b3ff8727f07fc133b2e84e909ec5f))

## [1.3.0](https://github.com/GoogleCloudPlatform/cxas-scrapi/compare/v1.2.0...v1.3.0) (2026-05-22)


### Features

* add `cxas trace` for end-to-end conversation observability ([a76c35c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a76c35c7cb1f25c9f6b4b953360817eaae0fa50b)), closes [#120](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/120)
* add DFCX conversation runner with live + history trace export ([#128](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/128)) ([5fb8a71](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5fb8a71acbdbf2b0ea6ef383a59ab17f48d71716))
* add optimize structure consolidator ([21e9667](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/21e9667c6e49ef2e36e76ea402699f88b1cc3aac))
* add rich response payload support to slot filling framework ([77edb75](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/77edb75b85b23e5b74ea457543c8bd3594cda538))
* add widget tool linting and core SDK deserialization support ([1863906](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/18639061fbb406c263af6c51121732b7dd2f02d0))
* added basic support for OpenAPI toolsets ([7a8e8d0](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7a8e8d0a242192b81ea7fcd3dc97bac689372a55))
* added fuzzy match for turn evals, case insensitive for contains turn evals ([bd1969d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/bd1969dcf0f48e14a62fb9afb14d8c4357b49ce1))
* **bella_notte:** event preemption, response propagation ([570e6ff](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/570e6ff60b6d1d2c521d527ec5179aad000d4632))
* **bella_notte:** event preemption, response propagation, and expanded tool tests ([a6631f8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a6631f8a0196625cb3c0c018228a07efe0a5dfb7))
* **bella_notte:** event preemption, response propagation, and expanded tool tests ([1a2fc50](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/1a2fc50da55d5075e8a89896466dda405be56c69))
* **bella_notte:** per-agent DAG tools, multi-slot setters, 3-tier steer-back ([f66f74b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f66f74bb66da2f7eb9f431a7a20c99901c3b8be2))
* **bella_notte:** structured logging, config validation, then_direct… ([d709a28](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d709a287d8c09f53a62606bbc011929fc9b6e9e0))
* **bella_notte:** structured logging, config validation, then_directive, engine hardening ([903d187](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/903d187bb2e8de6624b03c47f028b4c313b0d548))
* **cli:** Address PR 84 feedback and improve CLI robustness ([552c9c9](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/552c9c96dcdbac25c23e09a8455193cf87633611))
* **cli:** Extend CLI functionality for iterating on deployments ([160fab9](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/160fab91ffbc089fb7ba8b656a75e5d69dbb58ec))
* Consolidate and improve the usability protocols, and refactor the scoping layer to be cleanly structured as a first-class agent. ([4afb355](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4afb35533a0d71cefcf0bedd0de78f76936a79eb))
* Docs/slot filling guide ([b4c44bb](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b4c44bb38cad63ac01f101974f61fb9037010cc6))
* implement component linting ([56a6be2](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/56a6be29f21c6e94ce9c6f0202f8b0ba01f878c7))
* implement strict local GECX linter rules and scaffolder guidelines ([bc022c4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/bc022c41ae3af214c5698e5c7a470e7bcf96fcf5))
* implement strict local GECX linter rules and scaffolder guidelines ([3d5fe4d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3d5fe4d4a2c5dc2b23ab2cfa74e4080d313d9db5))
* promote migration helpers ([#137](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/137)) ([4bbcd29](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4bbcd29c5e6f9fb0752336c137bb6f9a2ce775db))
* SCRAPI CUJ Report Generator. ([03b8bf4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/03b8bf48a2092b1f806befff64cb3b9e836cf6ce))
* split DFCX→CXAS skill into resumable migrate/stage1/2/3 scripts ([eeadbe6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/eeadbe6f682f18ffe966cd35d367f5069cef11e1))
* split DFCX→CXAS skill into resumable migrate/stage1/2/3 scripts ([#130](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/130)) ([535299e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/535299e069a456b9ebb6697e7f2311a3bde80460))
* support OpenAPI toolsets in SCRAPI ([57c1058](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/57c10585bafc032b909e78e897abd1b4b3531640))
* support session variable accumulation in audio modality and pattern file filtering ([f9b452b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f9b452bf0d9f3365b24eb8cf9a7748685ff99a0c))
* support session variable accumulation in audio modality and pattern file filtering ([539212c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/539212c992ff779e04cd15ab5741c4414320ee47))
* topology linker html preview ([#138](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/138)) ([3c96ab5](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3c96ab58856b7685cfca21cc0be77240d6fc934a))
* **utils:** introduce request bucket RateLimiter for API client and … ([36b9f8f](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/36b9f8fd404d9820a381ea1fe53ca717b002d8b0))
* **utils:** introduce request bucket RateLimiter for API client and evals pacing ([81a3ee2](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/81a3ee2ab895844f66c9c320a5579d43aeacd020))


### Bug Fixes

* add widget tool linting and core SDK deserialization support ([dc87441](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/dc87441925aac32a134c12cd0263bb7d48c60475))
* deprecation warning for datetime ([12cc64d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/12cc64d7a375763c3d03489d49a37e15b7bbd56b))
* **deps:** update junit-framework monorepo to v5.14.4 ([13617cd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/13617cd7c8440472ae1fa1f39141c5b3d838d623))
* **deps:** update junit-framework monorepo to v5.14.4 ([abb7ade](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/abb7ade486aaa43ab61c8bdfc04f73b1155c6a7a))
* **deps:** update junit-framework monorepo to v6 ([2736e3e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2736e3eaf81e46c996924ff24a87dc04a4836184))
* **deps:** update junit-framework monorepo to v6 ([4906e01](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4906e0164268c29568d18ae338e9180e42555db9))
* **deps:** update junit-framework monorepo to v6.1.0 ([66e7567](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/66e756731c63dac428c60d53fbda6df2e6f72b64))
* **deps:** update junit-framework monorepo to v6.1.0 ([641f0d8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/641f0d8b983dd3adbfc6268e92de51b296f005bd))
* evals/turn_evals.py and utils/gemini.py lint errors fixed ([534c509](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/534c50934f4f5b7aeb4651063085dbab7abd50b0))
* implement thread-local Client in GeminiGenerate to ensure thread safety ([d53f768](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d53f768b8c2554a4f7252c3a424496f5e5cd3859))
* **linter:** Quick lint fixes in the CUJ report generator skill. ([f714910](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f714910dc9b8523317c2057a6bcaad2ad810efc6))
* **linter:** validate rootAgent JSON file ([#86](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/86)) ([fb36d51](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/fb36d5198b033e30fcd1b09fb3ffef700984668c))
* linting ([c9129e8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c9129e8ccadba8d7f10d4c618f29582506416596))
* lockfile ([67284d3](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/67284d365e859f53a970e106e35d9a23ee0f2246))
* make various fixes to optimize sim runner ([e463141](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e463141539a5fcc03481a39547a6771e28e05a04))
* move migration modules to src instead of skills ([251f0ac](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/251f0ac85505615c0a92bea9743567094e7b1402))
* remove files with just import in skills ([47ac0f7](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/47ac0f738830dc069de0466885bb6ba389112dc1))
* remove files with just import in skills ([0523dbe](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/0523dbe1a2f29e461917054accc5ed3e384f6a9d))
* **reporting:** handle multiline agent responses in html ([38bb898](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/38bb8981d2496965664e033c7c14c237ae88faec))
* resolve HTML link collisions and enhance turn metrics in combined report ([92ced47](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/92ced47d066eeb517430492fa16651a46d5a3cf2))
* resolve lint errors in app.py ([cb3ac31](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/cb3ac31f42c021dd381627f96558cae451eae267))
* resolve parallel simulation gRPC crashes and welcome audio disclaimer race ([7edd307](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7edd307b0ad23e2eb67f320e654254b90fe09a9c))
* resolve ruff lint and format violations ([bff83c2](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/bff83c2b73ba671b0ca32cc8a0b4029a7016c12b))
* sort imports in append_turn.py ([c1b5de4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c1b5de449eb5b17e20a0a206f22d378f6e1b5441))
* unit test fail,  grouping review moved under migration, inquirerpy dependency added ([cc85e09](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/cc85e09b1f7047c7b6ca20bbabb66184158f9caa))
* update lockfile and uv setup ([02a6aee](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/02a6aeea399e26439425db3eb09ed824b9ee6542))
* update variable references in cxas-agent-foundry skill and add e… ([86b7d68](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/86b7d68a7f172378088b37b341dee98665208c1f))
* update variable references in cxas-agent-foundry skill and add enum to core class ([7bf9c41](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7bf9c41496dafe845a76bca63432a6fa37229d5e))


### Reverts

* remove unrelated gemini.py thread-safety changes ([25c2409](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/25c24097523da5a50a89e1695a729b343158db15))


### Documentation

* add comprehensive slot filling usage guide ([acd1c31](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/acd1c3174426f2390a582a646326fd8e6c5f263f))
* add comprehensive slot filling usage guide ([a20a8aa](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a20a8aaec0cc7321b961fc1cbee0e6ec4e41656b))
* add Critical User Journey (CUJ) Transcript and Report Generator README ([6cc4bce](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6cc4bce3d7ca9f4c297425af967c845dbf3c8bd9))
* add DFCX migration guides, plus minor CLI configuration changes ([#117](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/117)) ([6e55b1a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6e55b1a7f6effd4dd67d900162f335c39abb4224))
* add download link for VS Code agent studio extension VSIX ([dcb227d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/dcb227d57edd7fb56c778bac2764af6c61752c5f))
* add download link for VS Code agent studio extension VSIX ([ca119b8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ca119b800654b0f9f338cc1a422224913dcf87fd))
* Add GIFs for VS Code extension installation and app import ([e67f6bb](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e67f6bb6a4a7b0d07d53b8529c7a231b9b447649))
* Add GIFs for VS Code extension installation and app import ([fb6ff2c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/fb6ff2ccfa1e4aeaab45d4fac8e14bb9144634de))
* add usage to docs ([5cdb089](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5cdb089c57d2c421f53a47163ee53c891f2ff3ad))
* recommend slot filling pattern for structured and sensitive flows ([da41102](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/da41102962d31099af87dd4ad52a61b1725b07c0))
* refine ingestion protocols and unify scoping layer into framework agent ([e3ed0ab](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e3ed0ab54e1c121d04e6fc48eedbd249c58be995))
* Update IAM roles to use roles/ces.* ([96a1b16](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/96a1b16f5c96a63841bfd50c40285d218653a82f))
* Update IAM roles to use roles/ces.* ([954ac5d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/954ac5d246f695abbc2fb8d2ee9d3e9ca0a61555))
* **vscode:** add VS Code extension user guide ([47d701a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/47d701a3757ccc5e5d0beab35e33d6a987d6e3a5))

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
