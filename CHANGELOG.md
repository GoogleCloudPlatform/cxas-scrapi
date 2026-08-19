# Changelog


## [1.9.0](https://github.com/GoogleCloudPlatform/cxas-scrapi/compare/v1.8.0...v1.9.0) (2026-08-19)


### Features

* **cli:** add --version-name flag to cxas push ([6e1a38a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6e1a38a06656c011e77261c3f9fc4c498f0ba6a6))
* **cli:** add --version-name flag to cxas push ([929d707](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/929d7078db95121b87dbc5d880a065703c01d073))
* eval coverage ([4f7b43c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4f7b43ca6adda0acad95a7e3654eee4e2ed1438c))
* **foundry:** add interactive evaluation reporter with LLM failure clustering to cxas-agent-foundry ([91997ea](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/91997eaad3c2fd65085c779a8b33b69cfa3de59b))
* **foundry:** merge interactive evaluation reporter into cxas-agent-foundry ([147dcc2](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/147dcc22154f830fed31ce275d464882aafceb4f))
* **migration:** build autonomous tool consolidation pipeline with deterministic alias resolution, improved acyclic topology wiring, and critical improvements and fixes ([#404](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/404)) ([2bbf193](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2bbf1935c2e337a1a59a8fb3a2ace25f525c9ecb))
* **tracing:** add audio transcription, WER calculation, and BigQuery table reprocessing ([536740a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/536740a584717aa071d58b78a279c861597cd63a))
* **tracing:** write updated turns to append-only BigQuery updates table ([20f2fcd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/20f2fcd8d397ccabc005579d55d1a3fce19f9105))
* Update turn evals for tool_not_called and not_contains ([2162038](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2162038d7f5e16ef187aca14090cfd0254e7d019))


### Bug Fixes

* **ci:** format code with ruff and isolate template file reads from mock_open ([3670138](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/36701388f6d0155f4ffd3bc156ba3e80668f923a))
* **tracing:** update default transcription model to gemini-3.5-flash ([57c2225](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/57c2225c497d1d46d3e3520af4d89a4e2e4a1f89))


### Documentation

* add quotas and rate limits guide to getting-started ([3379e91](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3379e91a1c6eac9c902ac652dc386f3fed2294c4))
* add quotas and rate limits guide to getting-started ([88bceba](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/88bceba059d907d32bb232bc94ab6acaa5ce40ad))
* align quotas guide with Gemini Standard PayGo usage tiers and official documentation ([1a81dc3](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/1a81dc30f7b61e6b01e0508ad554a1281a4e2bb1))
* **callbacks:** document preemption vs LLM re-invocation lifecycle and EMPTY_RESPONSE prevention ([f3e6ccf](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f3e6ccf1c35f604ca54f5083c6668421ab756f25))
* **cli:** document --version-name and versioning options for cxas push ([af98bc7](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/af98bc7c22f5b2b33818660680b230df7b59f5ad))
* **tracing:** document audio transcription, WER evaluation, and BigQuery reprocessing ([fab7925](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/fab7925825c233d9d55ad08e9176cece0822e133))

## [1.8.0](https://github.com/GoogleCloudPlatform/cxas-scrapi/compare/v1.7.0...v1.8.0) (2026-07-30)


### Features

* **cli:** expose --single-bidi-stream flag to evals report CLI ([e81878b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e81878bad503b021622cd6d85c0fad62de27d0cc))
* **cli:** expose --single-bidi-stream flag to evals report CLI ([93e8b7d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/93e8b7d047c79278e7ec4afff3413d7dd99cecd5))
* **evals:** add JSON output format for combined eval reports ([a857351](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a8573511f43c3b587b325921013ffcb4ebf5180f))


### Bug Fixes

* address runtime NameErrors and callback eval failures in CI ([32b5cf7](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/32b5cf79ce7fb8e34976ab9eec43ea0f21c027e9))
* **copybara:** address code review feedback ([c49b6f0](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c49b6f006f54104ed029661dc71f2954ec38719e))
* **copybara:** resolve pytype, testing, and struct issues for upstrea… ([17aff2a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/17aff2a67d9dd0635262bec99a66db38f1df2ab3))
* **copybara:** resolve pytype, testing, and struct issues for upstream 1.7.0 sync ([50379a4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/50379a4e0c1d1dc6ebc7257eb551ffd16ec87561))
* **cuj-report:** restore __future__ import ordering in skill scripts ([d6cf5a9](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d6cf5a98854d532c734a1295e66662fcf6927d79))
* Defer 1:1 agent push out of migrate.py to respect CXAS 100-agent cap ([838c140](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/838c1400dcf7e56bff0fe361c793d9715043ee0e))
* **deployments:** restore two-version minimum for traffic splits ([293337e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/293337ef0bb2c3425c4d8ffd45882a1f92ed0865))
* **evals:** apply CL 953545678 fix for turn 0 initial utterance in simulation evals ([7d60f9d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7d60f9d1ec8a7ff4c5205b92808b46ca7829fe23))
* **evals:** move EvalUtils static helpers back into class scope ([7159ca9](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7159ca90c0e284ed36d90445f2b8ca6721059556))
* **migration:** dynamically strip unimported DFCX triggers while preserving standard Python decorators ([4a1c209](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4a1c2092e92ccb797307b078872f337ba67bd9b8))
* **migration:** dynamically strip unimported DFCX triggers while preserving standard Python decorators ([53270bd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/53270bdb7fd7750f8974ead43a5a3bd6611f05cc))
* **migration:** runtime NameError (DFCXFlowModel is not defined) when cxas CLI initializes ([3297ac1](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3297ac18c04a58dbffdf240570333cf17c368b18))
* **migration:** strip all DFCX trigger decorators and preserve original function names for Python tools ([5c11aa8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5c11aa806f16d0c5293269e129bffde60f067784))
* **migration:** strip all DFCX trigger decorators and preserve original function names for Python tools ([510ddc1](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/510ddc1f5e17fa874fe378c6960a487404c8482e))
* resolve trailing ruff lint checks and CI failures ([266b882](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/266b882fd53b420b288f0092d50d96de15cb82f3))
* **test:** hermetic bidi session mocks & PyType signature compatibility ([1b9f9da](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/1b9f9dabf34dc0726f70f06f6804f7c4aa474220))
* **test:** hermetic bidi session mocks & PyType signature compatibility ([6d235a2](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6d235a2c03a51f592085494fcb102b264e58beef))
* **tests:** add missing return type annotation to test_session_id_extraction ([bcc041d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/bcc041d7151b9a4da4f68bd438f1933ba35f2806))
* **typing:** manually upstream copybara type ignores and overrides ([c737fcd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c737fcd7689426c952c231f0bd4ed42faebc01bd))
* **typing:** remove unused List import in traces.py ([4841bc1](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4841bc130ca76cca4e0dc00855723a8e3df9027d))

## [1.7.0](https://github.com/GoogleCloudPlatform/cxas-scrapi/compare/v1.6.0...v1.7.0) (2026-07-17)


### Features

* **linter:** add S008 rule enforcing single llmPromptSecurity guardrail per app ([ca377ae](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ca377ae361ad7a98624f9ea89bb0b153217319ed))
* **migration:** convert DFCX test cases to CXAS TurnTestCase simulations ([667e8cf](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/667e8cf7a18eef02173c82b7d08d5e55b77b0b01))


### Bug Fixes

* **deps:** update junit-framework monorepo to v6.1.2 ([bccb88e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/bccb88ec22f1fd370f5738f5940f870bb85fa010))
* **deps:** update junit-framework monorepo to v6.1.2 ([7d36d4b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7d36d4bf8b0cf6094d49519661fad28cdf4edba4))
* **evals:** prevent agent audio output file chopping in audio files ([5e6a4c8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5e6a4c89d47ade5553510ae8f5c47e806a6f03bd))
* **evals:** prevent agent audio output file chopping in audio simulations ([ac6f18f](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ac6f18fec872262d974bc036b45d949a2fb78fac))
* **migration:** prevent browser launch in headless context during tests ([4da8715](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4da87152bf33c388582a13604387ce1800e737ca))
* **migration:** prevent browser launch in headless context during tests ([d2b6fb9](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d2b6fb9a5c3dad16b2a8e9ae97013d0bb7a6090d))
* persist bidi websocket for audio simulations behind opt-in flag ([6682a83](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6682a837ad9e69a09cdbb758a5867aabbbba3258))
* persist bidi websocket for audio simulations behind opt-in flag ([6fa0399](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6fa0399399e1691411b48884630311fef8a03988))
* **setup:** Fix skill setup issue ([ed79706](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ed797063a33404d9bd000958dd73c394d9d6c1c9))


### Documentation

* **agent-foundry:** fix setup.sh comment to reference pyproject.toml ([5c03cc7](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5c03cc7b1f0e9609ddbaa02f00296b7dd2941fb0))
* **agent-foundry:** fix setup.sh comment to reference pyproject.toml ([f4f5caa](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f4f5caa01dda9ee6532530b78d28d157b974b601))

## [1.6.0](https://github.com/GoogleCloudPlatform/cxas-scrapi/compare/v1.5.0...v1.6.0) (2026-07-10)


### Features

* **core:** add dynamic voice_config configuration for client-side TTS ([244d629](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/244d62977eec28d52a553ffd278234661a0a5525))
* **cuj-report:** add ADK framework ingestor skill ([0e62b1f](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/0e62b1fb77bbd2885d8289f94539e3323fbd8999))
* **cuj-report:** add ADK framework ingestor skill ([5d29f05](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5d29f05c6fc41a0d8228cf1b260c6921657b00f6))
* **cuj-report:** add DFCX framework ingestor skill ([1131552](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/11315520f2582e1699eb150e526dded5bde4d8cd))
* **cuj-report:** add DFCX framework ingestor skill ([b625cac](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b625cacda69be9e93d1e6a84600b993b33150769))
* **evals:** add --capture-agent-audio flag to cxas evals report CLI ([8dd9901](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/8dd9901dab6e3a723a47f5a9dd5210fde851dd6b))
* **evals:** add --capture-agent-audio flag to cxas evals report CLI ([22db38c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/22db38c314d5e494008eb4e8b409891ef0dc4274))
* **evals:** propagate voice_config parameter through simulate_conversation ([08aaa27](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/08aaa278a10cc484dd42848ebd02ba7eac7ec521))
* **evals:** Support Multilingual TTS for Simulations ([75d9f85](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/75d9f858f3d2a6b994523f142ddfbaa3d81f5f3a))
* Expectations can be judged based on audio token (`inline_data`) ([882e1a0](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/882e1a05728a8cd8a1e29387fe8039112e23c86e))
* **insights:** add CCAI Insights topic modeling, scorecard activation & deployment, analysis rules, and interactive HTML dashboard report ([78a53ba](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/78a53bab0cfd51202882ec7942db261e30926b92))
* **migration:** add interactive Agent xprs designer and utterance harvesting gated behind --experimental-agent-xprs ([#325](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/325)) ([c875c40](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c875c406183c9d2de6c4576fcb380d0429d49c76))
* **runner:** support voice_config parameter in EnhancedSimRunner ([d69fed6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d69fed6f7884909a119c15db489a80f365aac646))
* **skill_eval:** add 10 new evaluation scenarios, multi-phase latency profiling, and SUT environment isolation ([999befb](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/999befb5ac34bb0ad56243124839bf137670ccad))
* **skill_eval:** add generative scenarios and timeout/interactive fixes ([3164fad](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3164fad67e24ff9c070af3716775ff02cad7beef))
* **skill_eval:** add multi-phase latency profiling and timer telemetry ([52f0034](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/52f00349516af9d0416fa9422b13ab9eaeedae21))
* **skill_eval:** enforce strict SUT environment isolation with dynamic uv index credentials ([2d8899e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2d8899e3f2fca084e7a11399d604d1171230fc27))
* **skill_eval:** introduce automated Antigravity CES skill benchmark suite and scenarios ([f0d4f6b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f0d4f6bd168b7a3d26242efac1b488b21b880743))
* **skill_eval:** introduce automated CES skill benchmark suite and scenarios ([c911b18](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c911b18916fd3e0a8a5bc81b0225897bd57afb95))
* support deployments in scrapi-sim-runner ([cb3916c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/cb3916c0c481e8310107f8532c03e4fb2529a202))
* support specifying the deployment in scrapi-sim-runner ([c74feb6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c74feb6c8f444a7cb135508bea52c8e37e37281a))


### Bug Fixes

* Add fake tools flag ([e2582fd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e2582fd2fc5095184d64b7c2d249ce31b7eda7ac))
* add optional flags to use tool fakes ([1503fbe](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/1503fbe870bb8d6bf1816a6ea873700a2b8365c3))
* created expectations only flag to ignore goals and updated goal … ([#322](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/322)) ([c58efa9](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c58efa9795407422c2f647846f46d980c452e6de))
* **deps:** update junit-framework monorepo to v6.1.1 ([5eae2a1](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5eae2a1eb79a9c3eb76f99412b085593326b88f1))
* **deps:** update junit-framework monorepo to v6.1.1 ([75aba00](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/75aba0013fc9831939e3019c309840cafb5d6625))
* **evals:** accept and forward progress_callback in reporting.py ([a9513a6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a9513a685c931c0decc6968de0f8baeb12158f62))
* **evals:** accept and forward progress_callback in reporting.py ([c735944](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c7359442643fbebde30bad62da720fe58ac0cba3))
* **evals:** resolve linter errors and update test assertions for dynamic voice_config ([260f8b2](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/260f8b2dfcc93dfa65e2301728f17c72095871a2))
* extract voice_config from test case in simulate_conversation ([c3523ca](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c3523cacf6b4a346e3fca73a5bb20f0830defd8c))
* extract voice_config from test case in simulate_conversation ([33a0fc0](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/33a0fc01e1d182b1b2f9ce9e90bb0d77cef75ddf))
* improve simulation retry logic ([0ed7785](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/0ed778538885b08e74396ed6b1329d21decb30f4))
* improve simulation retry logic ([fbac7c7](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/fbac7c74a67222b676ab8a6b464391343aa61923))
* **linter:** resolve generic search description linter false positives and add type-safe JSON checks ([ab98f17](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ab98f17be19176cfddb6132a087d1659d7d7a47c))
* load common expectations ([5b4eece](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5b4eece90a4e87ece02ccdbda3197500ba0b8ffe))
* load common expectations ([c33b838](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c33b838ce77978c4fcee247bcd5ade01c13b4eb3))
* persist bidi websocket for whole session ([f2a63d4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f2a63d48fc0e4bb32065ffe5ebd3914c8a289cb0))
* remove files not related to change ([8d598f5](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/8d598f595bbaf0d4763a3fcdc07ccfa62720cdfb))
* **reporting:** add expectations_only to legacy run_all_evals wrapper ([6d8dfe4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6d8dfe48bdbf0bc5f5b783d645cde67bbb0ade3b))
* **reporting:** add expectations_only to legacy run_all_evals wrapper ([b399281](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b3992816f0226e2c51495f2ace36f21e1c815a6e))
* revert goal prompt for simulations ([8a99cac](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/8a99caca8e69b420d44eb895b0f271555748049b))
* revert goal prompt for simulations ([1deb629](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/1deb6297b7078f0912914e5ad6d2027f3a12d4b5))
* **skill_eval:** address PR [#328](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/328) review feedback on Timer and add unit tests ([41380de](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/41380de9fa58336d355b05b90c4bc469e05b2ec0))
* **skill_eval:** clean up SUT virtualenv on head close to prevent tmpfs inode exhaustion ([9059a20](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/9059a208ddaac78cca0acefecc8b06b996fd92ce))
* **tools:** add fault tolerance around retrieve_tools in Tools.get_tools_map ([81e6482](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/81e6482939eb44390d5e447cf4157a5b8650cbb8))
* **tools:** add fault tolerance around retrieve_tools in Tools.get_tools_map ([272ef98](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/272ef98bf968a7b903d22cee6138cbf98dfbc86c))
* **tools:** catch specific Google and requests exceptions instead of generic Exception ([1e8ddcd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/1e8ddcdd04073ec52e2e7a27aa88c35e8e027d00))


### Documentation

* add IAM permissions guide and update navigation ([5697ab5](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5697ab58ecba392af322e8458b9e06647d2cd585))
* add user guide for A/B testing and traffic splitting with SCRAPI ([2a55ad5](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2a55ad56614b9c4e1e746a3523f3ef18c4f82c5b))
* document voice_config parameter for local audio simulations ([25063e8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/25063e865fcf6f7294df21f88774e1fe29b6f1f7))
* Update linter rules documentation ([e524788](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e52478823a33ec3f75cf4fc370e2629fd3f718fb))
* Update linter rules documentation ([35bdd9d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/35bdd9da01977594f04659ae3894a57a908e073e))

## [1.5.0](https://github.com/GoogleCloudPlatform/cxas-scrapi/compare/v1.4.1...v1.5.0) (2026-06-26)


### ⚠ BREAKING CHANGES

* **migration:** always-on consolidation + grouping gate default ([#231](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/231))

### Features

* add voice background noise and randomized burst artifact injection ([80258b8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/80258b8c6121c8675868637d07599eb6cae85067))
* address PR review comments on noise injection ([7b8e84e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7b8e84ee2228f4d0fb480b6ea5febed599c1bc88))
* **agents:** add CUJ standardizer agent, refactor framework detector, and compile deliverables ([49e5f55](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/49e5f558e9a6145fe064a494cb7b76b10934e107))
* allow customizing models for running and for evaluating evals ([eda8f24](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/eda8f249853f6be93814715923ca7fea6627f4f6))
* **cli:** add --overwrite flag to cxas pull ([029edb4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/029edb495542165b4938e6de01c4dbdd36a4b471))
* **cli:** add --overwrite flag to cxas pull ([4d14e96](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4d14e96a7669a05fa72009c28c3bd82280b9b1f6))
* **cli:** add --overwrite flag to cxas push ([3d0287e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3d0287e8458c7f75cb0639774919d91ab6dab509))
* **cli:** add --overwrite flag to cxas push ([7b4b24e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7b4b24e8ae8e8bdb483b627843688bc9ae8365a1))
* **cli:** add --timestamped flag to evals report ([19e528d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/19e528d424bebda1eb5a04f0eaeb9173a9ea6bb4))
* **cli:** Add no-input flag for headless execution ([408a564](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/408a5640efc3d00916c75955473689a98d358ff4))
* **cli:** flag variable update attempts in llm-lint ([e3bc802](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e3bc802b0af300da7b7816224fccc08b82622608))
* **core:** add WebSocket timeout protection, close diagnostics, and support for citations and custom payloads ([aff41c8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/aff41c8f2cfe08e8858c24cca777ee61fd610f66))
* **core:** add WebSocket timeout protection, close diagnostics, and support for citations and custom payloads ([ca29e81](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ca29e81de256ee2f9ed125635d310250ba7fed64))
* **cxas-cuj-report-generator:** enforce explicit time confirmation turns in voice prompts ([f3c360e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f3c360e9c1472d77c6c0975d5570bdce0d13b4bd))
* **cxas-cuj-report-generator:** enforce spelled-out verbal forms for times in voice responses ([2f7ae2f](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2f7ae2f59fda9ead086dc97290b2f19bd8a02ab3))
* **cxas-cuj-report-generator:** implement batch grading, prompt optimizer, and TESTING manual ([f822537](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f8225370bbc9d9418c3947487fbd2fa68b5246e7))
* **cxas-cuj-report-generator:** integrate graph traversal rules, voice realism guidelines, and naturalness standards ([3a0eecd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3a0eecd98e906139b06b10c404db2a0fb56d8f00))
* **cxas-cuj-report-generator:** mandate root-level call fields, generic fallback greetings, and sign-offs ([5853b19](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5853b1940d8d1e3ba098789173507ce42bbc7ae8))
* **cxas-cuj-report-generator:** mandate spelled-out forms for percentages, order IDs, and reward counts ([b777c50](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b777c50f39e10d5cabc10bf8742a8496839a9543))
* **cxas-cuj-report-generator:** require polite voice markers on every agent turn ([7ae2c77](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7ae2c778ce0e0047f6ff3f9689d12a7eeec7e3e2))
* **cxas-cuj-report-generator:** standardize sign-off phrasing to Dining Service goodbye ([3df284b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3df284b5575055864942e9de529459fc0372fb8e))
* **deployments:** add traffic split support ([5806bba](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5806bba462b1fc55d146d110dfa1d3810d2b5c39))
* **deployments:** add traffic split support ([ed10846](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ed10846b2d306aff2bb5d4c45eaf016342000b7d))
* **evals:** add ability to timestamp eval reports ([e695201](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e695201c3a910218c1b3bd334fe033f0f694ffe2))
* fix smaple names ([2bf98d8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2bf98d886c07a41106e6f83445f6a38a9caa91d2))
* **guardrails:** add design guide docs and local create scaffolding ([d3ef0c4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d3ef0c44f80f9300516a8e52c70becf4afd93900))
* include global instructions and dynamic DIs to llm-lint ([7ed70b3](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7ed70b3c3f0efbfd3ee36593e85875bf4a52dac1))
* **ingestor:** enforce first-turn description validation in append_turn.py ([12487f9](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/12487f91dd735035a549d9c869fcdfe34d355823))
* **lint:** add I016 prose-state-machine rule ([399d664](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/399d664d9e0d7373732d62fb33ca6889bd617883))
* **lint:** add V100-V104 variable-reference rules ([#291](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/291)) ([8bfc92d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/8bfc92d0c138deeb15a96cd89d959db7aeeb6568))
* **linter:** Add S007 linter rule to ensure sub agents only have one parent ([b844aff](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b844afff3c7def8dc4e2dc4f924de0aaaf2abcfd))
* **linter:** flag variable update attempts in llm-lint ([c4bc3a1](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c4bc3a1c11767bdaaf470bc6d65dd61fe8efbb7d))
* **linter:** Validate single-parent constraint for sub-agents (S007) ([cfacdfb](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/cfacdfb9d037222142e1bc9735c26902c2beac6b)), closes [#301](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/301)
* **llm-lint:** expand semantic linter with red-teaming & robustness scenarios ([2b1b988](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2b1b988def73fa82f53b75a8a91e543a5a46f88c))
* **llm-lint:** expand semantic linter with red-teaming & robustness scenarios ([9e7768a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/9e7768a727983aa95ea7d0b96b5894c0f03e7618))
* Make generated local templates pass lint by default ([97ceabd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/97ceabdda3af98b424df241d71fab65f7bf087d6))
* make non-interactive and headless execution safe ([#55](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/55)) ([c61f9ab](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c61f9abc2909e07a909d15dd702536bb5aaad6ab))
* make use_tool_fakes as a flag when running evaluations ([3138fcb](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3138fcb35374aaafc9bc12f4fd3bb3da2b87abfc))
* **migration:** always-on consolidation + grouping gate default ([#231](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/231)) ([abec278](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/abec278fb3de5338147d4d3c43385113ef87c262))
* **migration:** emit tabbed HTML analysis report on every migration ([#207](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/207)) ([85bebc1](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/85bebc1e51c1e6362e689fbdb0cb77b94fb1df35))
* **migration:** file-watch fallback for grouping gate ([856248d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/856248dca50ece0ae5f755d0141e38242f47b39e))
* **migration:** Grouping Review tab in analysis report (read-only) ([3458903](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/34589032bbbcf6576a313ba9aa3d538f8f2dd7c7))
* **migration:** Grouping Review tab in analysis report (read-only) ([#227](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/227)) ([58c49da](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/58c49da21d664b9a9545233672d597dde0c8c9e2))
* **migration:** HTML grouping confirmation gate (opt-in) ([7f2692d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/7f2692d5f291cd8c30618a38cc6d691784edf1cf))
* **migration:** implement consolidated agents name-mapping throughout agent config ([0c325c3](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/0c325c35455d2915441632852ba476fe076ad3ce))
* **migration:** local-first Phase 8 — skip per-flow CXAS deploys until post-consolidation ([05cf6a4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/05cf6a4351c712058d2f96570b38c23ab3e88375))
* move llm-lint from a skill to a package prompt and register as a cxas CLI subcommand ([9f36128](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/9f3612820e431a2bf72ea74e78853d06d169ff0e))
* **prompts:** add rules for quotes vs back-ticks and variable formatting to llm-lint ([8d00356](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/8d003561ed795422c3e03198277e630f65462a3d))
* **report-generator:** integrate export transcripts to JSON feature in HTML report UI ([9e5b8ad](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/9e5b8adf392c9ba45375b7b8483b0fa7b8ee4898))
* **supervisor:** implement ingestor supervisor and dynamic batch execution daemon ([827bc5e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/827bc5e912f40f8189c1be5852424a35feffa311))
* **trace:** add 'cxas trace search' for full-text conversation search ([536fdb6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/536fdb673ad03f1c5212f60f26d8173f4465f51c)), closes [#236](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues/236)


### Bug Fixes

* add background_noise_file nd additional args ([1ea3e57](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/1ea3e57998bd957aac6573f1c9bdce1abd09d6fd))
* add background_noise_file nd additional args ([3dd61b1](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3dd61b14bd07cf54a6731eef8cb9cd70e90a357e))
* add pre-commit hook to block brand names ([0b79d39](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/0b79d3968dd3be4fa514967e6728948b923bb481))
* add pre-commit hook to block brand names ([3f01d47](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3f01d47db94de0b36dd387983e215fb923193621))
* add unit tests ([1925fc3](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/1925fc38475a0e0a8d504acaf725120ae18ec87b))
* address final E501 line length errors in voice noise PR ([a17c288](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a17c28876109122050bf57a5effeca3c32d9e29a))
* address lint failures and update unit test assertions ([80a5468](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/80a5468cf71ec878120631536e4aab86b119429b))
* clean up example colab ([06d1079](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/06d1079430fcb01d7a669089adf035e039737db8))
* clean up example colab ([2c68e53](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/2c68e53a93b6f073239fefc592faed9c49aff471))
* clear uv and venv ambiguity ([013cafe](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/013cafe7ed0e4fae0dff4fa9af6660865b0b9124))
* **cli:** drop unsupported format kwarg from evals report ([ee058ba](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ee058ba686d0be2e93360003c238cf19e84c55a6))
* **cli:** fix line too long in help text ([fc3e1b3](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/fc3e1b3777ab9c922ed55f592a4ce24ea4f61821))
* **cli:** forward modality argument to run_evaluation in run_eval ([f0f66d4](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f0f66d45d22e660de2b920956889e2bc86fe4856))
* **cli:** forward modality argument to run_evaluation in run_eval ([e288802](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e288802f2b36d0c1fc2f1a4189bedaccb0e06ff3))
* **cli:** resolve ruff line length and mkdocs strict nav warnings ([aad1066](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/aad1066325d30b2ee2f0732efdb0eeb9980ec7ce))
* consolidate the detailed trace to populate the json for html ren… ([8f66b86](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/8f66b8617d1070bfca5e97a07263c0b373f6b951))
* consolidate the detailed trace to populate the json for html rendering ([b3f965a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b3f965a8c4d8adac0557042ce365ecb0b24df198))
* **docs:** add missing trace command to mkdocs navigation ([df7a03a](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/df7a03a1b91eea8f1e225e09e7afb6aa877386a5))
* **docs:** resolve strict link and anchor warnings in patterns and tutorials ([d2ed4eb](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d2ed4eb69dec0ba845312b33bcc17507a9b402f1))
* enable tool fakes for cxas evals report and update docs ([544ae40](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/544ae4075e31f97eb9f5488a08e85b9fc4e09248))
* eval cmd remove dead format flag ([65cf240](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/65cf24036b259ec0af3b93adb909ea3d4b057b7f))
* **eval-utils:** Propagate credentials to internal Evaluations client ([4054594](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4054594b9f4337a517e33d604afc83560fd16fa8))
* **eval-utils:** Propagate credentials to internal Evaluations client ([c9ffa16](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c9ffa16f4a93db4ac279c61d127a124be03d7955))
* failing test due to merge conflict ([30240bc](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/30240bcd866f7ad85a2ca711b8720a8cf31b37d3))
* fix goal progress status on session end in SimulationEvals ([79c2ca8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/79c2ca85a2e60d87b85e53a3f4ed503f5f4faef1))
* fix smaple names ([c0a3c88](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c0a3c8885d505126ce97f36d2c3d77d27ba5fd1d))
* formatting ([32ce5ee](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/32ce5eee198663cbb1ee2a3fd83ec2aaed1faa8b))
* **gemini:** log traceback using logger.exception instead of silently returning None ([643ccd6](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/643ccd6bf6b256778f67abade933aee1873815fe))
* lint issue ([5c4c7cd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/5c4c7cdae5884e1a9e9489854f0d4dde5462989f))
* **linter:** bypass scenario validation on golden/deterministic evaluations ([c7629d8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c7629d8b870652934f681a562a0f180d7a3c77ea))
* **linter:** fix false positive V006 linter errors on deterministic Golden evaluations ([339bf88](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/339bf88c375ba29ac0992e27550d3322b38f1a2c))
* **lint:** update I014 to check for {current_date} instead of ${current_date} ([13a203b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/13a203bfbb29d26858a6a269cddda684f88de60d))
* lock changelog edits ([a885755](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/a885755f3e197351226c33e9d1b9e85831562e35))
* make use_tool_fakes as a flag when running evaluations ([933edc9](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/933edc9ed615ac596e36d183d844b871ec40326a))
* **migration:** canonical-XML consolidation prompt + lint-driven retry ([8bca323](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/8bca32329f36473f6365dd18f9e442bc78d3c703))
* **migration:** clean up unused imports and fix Gemini cache content format ([c2f4f38](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c2f4f384fe72df2ba3d9c9eb93aa38aff721f109))
* **migration:** resolve lint errors and pydantic Optional NameError ([cd4627f](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/cd4627f4d43c777a4bb2eaba19f2c6265520be72))
* **migration:** resolve tool name truncation, standardize XML instructions, add LOCAL state ([0312200](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/03122001718cbe4c63ea686dd6cc1662d678d5dd))
* **migration:** resolve web review server blockers, TUI wiring gaps, and enable early boot dashboard ([b1928b5](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b1928b57fcd12ae45e334fa14499409f7f036397))
* **migration:** restore docstring and inline comments in structural_consolidator ([e21b407](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e21b407e69431cbf40003864fec2ac75aedeb7bb))
* prefer always using 'uv run' for 'cxas' commands. ([835e63f](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/835e63fcf6d2f4b1909fa5d72a345620c3df7658))
* Print trace when logging exception error messages to prevent silent errors. ([3adbb4c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3adbb4c89c2eee40b9725f0c3b9d4551a5e2859e))
* **prompts:** lowercase &lt;Agent&gt; XML tags for consistency ([52c4c2e](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/52c4c2e52bd0a090393d0d84c2af41758e479fe8))
* recover from bad truncation of cuj skill. ([0719903](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/0719903f8922ce930f6aab3f0637a5b5c6806090))
* reformat files based on ruff check ([478c7f8](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/478c7f8cc6d546f84089fde1445f32d20e4aaf7b))
* remove duplicate agent response in turn evals ([cff8c5d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/cff8c5dba83f1bed5b354754cfbb812cdd290782))
* ruff linter check ([9b05314](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/9b053147857fbf297ca55a8193beab5b2285a33c))
* **skills:** pass resource name instead of display name in sync-callb… ([f904f76](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f904f7691f463f90b40fa466e8a896d6f278cd76))
* **tests:** update mock app_name placeholder format to pass validation in test_eval_utils.py ([3a75617](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/3a756179d827f25758bb989b610d846223f270aa))
* **utils:** add missing Any type annotation to kwargs in EvalUtils ([cf8d9de](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/cf8d9de07bbc7b0443a1668ec3857812a35a4aff))
* xml schema error ([743100d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/743100d09e56d85fca859fcd26c4472e4bc1f3f6))


### Performance Improvements

* **cli:** implement lazy imports for CLI subcommands to improve performance ([18eeb31](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/18eeb316134ef1609f7025f8462fe758c50b5843))
* **cli:** implement lazy imports for CLI subcommands to improve startup time ([acdc8cd](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/acdc8cdd12832750163eeb7feeaa9d1bd97a425c))
* **migration:** reduce pipeline latency with concurrency, local-first Stage 1, and prompt caching ([13ec16d](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/13ec16db2aeec562e54d3da3b5accae0c3469bf8))


### Reverts

* remove unrelated setup.sh change from cleanup commit ([6d7df9c](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/6d7df9c8b96cc4c09851dc7d5bd1b689da8d909b))
* undo changes to sync-callbacks.py ([c9f543f](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/c9f543f0969f4c9b247a21abd9e9dd232f1808df))


### Documentation

* add agentic development environment setup tutorial ([ef1cc97](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/ef1cc97fc6c6d9eb6a05403b1b51ebc10ef67463))
* add agentic development environment setup tutorial ([60478db](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/60478db65f4e31d3c50a1fe149d2204367cf473e))
* add type hints and docstrings for noise injection params in reporting ([b671ada](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/b671ada2a6819f33f1f2a1aa338845ffe0c29851))
* **cli:** add llm-lint documentation and interactive help subcommand ([1d0d936](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/1d0d936752485b2a38551b5c655c9a170ece691b))
* **cli:** add llm-lint documentation and interactive help subcommand ([e473984](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e4739846185fd5fa06eb3f27cbc7a553ca0c9e6c))
* **cxas-cuj-report-generator:** align testing documentation with new python evaluation pipeline ([e38fad2](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/e38fad218ab392078fe0091454f9ef26af076142))
* fix app-dir argument typo in linting guide ([4bcf4bb](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/4bcf4bb089b6057dd4d30cd938ecb4f00d177dc9))
* make mock tools it's own document and update refs ([d61354f](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d61354f6afdd9631335e56a14d897f8d48528212))
* update running-evals-cli reference and add tool fakes documentation ([f3cb957](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/f3cb95784fceba7baa523f16bb131c9f59a5c671))
* update running-evals-cli reference and add tool fakes documentation ([d2a9120](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/d2a9120846fcb6c42f0470ed570de8199748ecd0))


### Miscellaneous Chores

* force release version to 1.5.0 ([837a97b](https://github.com/GoogleCloudPlatform/cxas-scrapi/commit/837a97b5976a7c19f9ffa43c50b78d833ed4be28))

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
