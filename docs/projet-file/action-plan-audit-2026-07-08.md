# Plan d'action — audit externe 2026-07-08

> Audit « regard neuf » (architecte externe, sans connaissance préalable) réalisé le 2026-07-08
> sur la branche `chore/post-v0.5.0-cruft-sweep`. Baseline vérifiée : **399 passed, 24 skipped**,
> ruff clean, `~2.2 s`. Ce document traduit les constats en backlog priorisé **et** en boucles
> (« loops ») outillées, selon les bonnes pratiques Claude Code (turn-based / goal / time / proactive).
>
> Il complète — il ne remplace pas — `audit-post-v0.4.0.md` et `audit-followup-triage-v0.2.0.md`.

---

## 1. Le constat central (à lire en premier)

La suite est **verte, rapide, déterministe et de bonne qualité** — mais son vert **surreprésente la
confiance dans la couche DDS live**, qui est le cœur de la proposition de valeur marketing (« multi-vendor
OMG DDS-RTPS »). Trois faits se combinent :

1. **Les deux adaptateurs DDS réels (`dds_cyclone` ~743 LOC, `dds_fast` ~712 LOC, ~35 % de `src/`)
   ne sont jamais exécutés** : ils importent leur binding au niveau module, et les bindings
   (`cyclonedds`, `fastdds`) sont absents en CI comme en local. Même leurs helpers purs
   (normalisation QoS, extraction GUID/vendor) sont donc **inatteignables** par les tests.
2. **Le décodage des samples DDS sur topics utilisateur est non-fonctionnel en live** :
   `_try_dynamic_decode_fast` renvoie toujours `None`, et le repli `annotate_raw(b"")` produit un
   `_raw_bytes_hex` **toujours vide**. Le repli documenté (« récupérez les octets bruts ») est
   impossible.
3. Le tooling de couverture (`pytest-cov`) **n'est pas installé** : aucune métrique de couverture
   ne cadre ce trou.

Conséquence : des bugs fonctionnels (fréquence `topic_metrics` fausse, gaps de séquence explosifs,
faux-négatif QoS Deadline) ont pu **shipper au vert**. La priorité n° 1 n'est pas un fix ponctuel,
c'est de **rendre la couche DDS testable** puis de la verrouiller.

Le reste du code (ROS2 CLI, services, modèles, télémétrie, wiring, CI/CD, sécurité) est de **très
bonne facture** et confirme les deux promesses affichées : *read-only by architecture* et *Windows-first*.

---

## 2. Backlog priorisé

### P0 — À traiter avant toute publication marketing du volet DDS

| ID | Constat | Fichier(s) | Action |
|----|---------|-----------|--------|
| P0-1 | Helpers DDS purs inatteignables → normalisation QoS (feeder du diagnostic phare) non testée | `dds_cyclone/adapter.py`, `dds_fast/adapter.py` | Extraire `_cyclone_qos_to_profile` / `_fast_qos_to_profile` / `_extract_guid/_vendor_id/_hostname/_topic_name` vers `adapters/common/` (comme `cdr_decoder`), puis `tests/test_dds_qos_normalization.py` sur objets factices duck-typés. |
| P0-2 | Décodage user-topic non-fonctionnel ; `_raw_bytes_hex` toujours vide | `dds_fast/adapter.py:492`, `dds_cyclone/adapter.py:551`, `cdr_decoder.py`, `xtypes.py` | Soit câbler les octets CDR réels dans le repli, soit **rétrograder honnêtement** README + descriptions d'outils (« payload user-topic pas encore disponible en live »). Ne pas laisser la prose promettre un repli impossible. |
| P0-3 | `pytest-cov` absent → aucun garde-fou de couverture | `pyproject.toml` (`[dev]`) | Ajouter `pytest-cov`, publier un seuil (au moins sur `services/`, `adapters/common/`, `config/`), afficher `term-missing`. |

### P1 — Bugs fonctionnels d'outils livrés

| ID | Constat | Fichier | Action |
|----|---------|---------|--------|
| P1-1 | `topic_metrics` : fréquence fausse (off-by-one **et** lignes de snapshot au même timestamp) | `metrics_buffer.py:157-164` + `_peek_builtin` des 2 adaptateurs | Fréquence sur instants d'arrivée distincts, `(N-1)/(newest-oldest)` ; ou masquer `frequency_hz_observed` pour les sources snapshot. |
| P1-2 | Gaps de séquence explosifs (wrap 16-bit, restart, multi-writer) | `metrics_buffer.py:217-237` | Grouper par writer GUID (ajouter le champ à `MetricsSample`) + détecter reset/wrap. |
| P1-3 | Faux-négatif QoS Deadline : reader fini + writer `None` (=infini) non signalé incompatible | `qos_analyzer.py:74-79` | Modéliser Deadline absente comme infinie dans la comparaison RxO. |
| P1-4 | `LifecycleBuffer._participants` non borné (fuite mémoire + bloat de sortie) | `lifecycle.py:59,193` | Cap LRU du dict / purge des `"left"` > fenêtre de rétention ; cap sur `snapshot_participants`. Corriger le docstring « bounded ». |
| P1-5 | Cyclone `take_iter` destructif + reader neuf par appel → flapping discovered/lost | `dds_cyclone/adapter.py` (217,242,332,366...) | Utiliser `read_iter` non-destructif ; reader builtin persistant par instance. |
| P1-6 | Dérive doc : `product-plan.md` §1/§4/§11 dit encore « five tools today » et sa **propre barrière de gouvernance** (« any 9th tool needs an explicit re-scope discussion documented in this register before code lands ») a été franchie sans réconciliation | `docs/product-plan.md` | Cascade docs-curator : aligner §1/§4/§5/§11/§13 sur les 11 outils livrés. Idem CLAUDE.md §2/§12 (même si local). |

### P2 — Robustesse, dette, hygiène

| ID | Constat | Fichier | Action |
|----|---------|---------|--------|
| P2-1 | Duplication ~40 % entre Cyclone et Fast (~250-350 LOC) | `dds_cyclone`, `dds_fast` | Base partagée `_DdsObservabilityBase` (validation domaine, 5 raisers ROS2, squelette `detect_qos_mismatches`, `participant_events`, `topic_metrics`, boucles metrics). |
| P2-2 | Stubs OpenDDS/Dust 95 % identiques ; `OpenDdsAdapter.is_available()` peut mentir (True alors que tout raise) | `dds_opendds`, `dds_dust` | Base `_StubAdapter(...)` ; `is_available()` OpenDDS → `False` tant que non implémenté. |
| P2-3 | `vendor` Literal (`cyclone/fast/rti/mock/unknown`) désaligné de la matrice 8 vendors | `models/schemas.py` (`ParticipantInfo`, `ParticipantEvent`) | Étendre le Literal (ou mapper vers `unknown`) avant que opendds/dust/opensplice/coredx/intercom émettent des participants. |
| P2-4 | I/O bag réelle jamais exécutée (rosbags pur-Python, absent) | CI | Ajouter `rosbags` à un extra CI pour lever le skip du seul test I/O réel. |
| P2-5 | Divers mineurs | — | `iter_field_names` sur `__slots__` string (`cdr_decoder.py:94`) ; `frequency_hz_declared` jamais peuplé ; `_encode_raw_bytes` hex-puis-tronque ; cap profondeur récursion `decode_field_value` ; `_KNOWN_TOOLS` sans `peek_bag_samples` ; import privé `_DDS_BACKEND_MODULES` ; dead code `parse_echo_yaml` / `_ = (...)`. |
| P2-6 | Durcissement hosted (déjà DEFER en triage) | inspector, ros2_live | `Path.resolve()` + racine autorisée pour bag ; rejet `-`-préfixé ; env-scrub subprocess. **À laisser DEFER tant que mono-tenant local.** |

---

## 3. Stratégie de boucles (« loops ») par chantier

Rappel du cadre (article Claude Code) : on choisit le type de boucle selon *ce qu'on délègue*.

### 3.1 Turn-based + skill de vérification — pour les fixes de code (P0-2, P1-1..P1-5)

Le point faible révélé par l'audit est **l'étape de vérification** : `make check` est vert alors que
la couche DDS est cassée. On encode donc « ce que veut dire *fait* » dans un skill, pour que Claude
s'auto-vérifie au lieu de se fier au vert.

→ **Artefact créé : `.claude/skills/topicforge/verify-change/SKILL.md`** (voir §5). Il impose
`make check` **plus** : couverture sur les modules touchés, smoke-test mock-mode réel, et un
garde-fou explicite « vert ≠ DDS live vérifié ».

### 3.2 Goal-based (`/goal`) — pour le chantier testabilité DDS (P0-1)

Critère de sortie déterministe, idéal pour `/goal` :

```
/goal Extraire les helpers QoS/normalisation de dds_cyclone + dds_fast vers adapters/common/,
      ajouter tests/test_dds_qos_normalization.py. Stop quand : les nouveaux tests passent,
      pytest-cov montre >90 % de couverture sur le module extrait, et `make check` reste vert.
      Stop après 6 itérations sinon remonter le blocage.
```

Même patron pour P0-3 (« stop quand `pytest --cov` tourne en CI avec un seuil publié »).

### 3.3 Time-based (`/loop`, `/schedule`) — pour la dérive doc et le CI-watch

- **Dérive doc (P1-6)** — récurrent, entrée qui change (compte d'outils, schémas). Cadence lente :
  ```
  /schedule chaque lundi : lance le sub-agent docs-curator sur README, product-plan.md, CLAUDE.md,
            CHANGELOG. /goal : zéro divergence de compte d'outils / de version / de schéma entre les
            quatre. Ouvre un diff-only, ne publie rien.
  ```
- **CI-watch** (au moment d'une PR de fix) — événementiel plutôt que temporel :
  ```
  /loop 5m vérifie la PR courante, corrige la CI qui casse (matrice 6 cellules Win/Linux × 3.11-3.13),
           réponds aux commentaires de review. Stop quand la PR est verte et sans commentaire ouvert.
  ```

### 3.4 Proactive — après publication (funnel bug reports)

Une fois publié sur PyPI/marketplace, le flux « bug report » est récurrent et bien défini :
`/schedule` (triage) + `/goal` (« ne t'arrête pas tant que chaque report trouvé n'est pas trié,
actionné, répondu ») + workflow (explorer plusieurs fixes en worktrees parallèles, juge adversarial)
+ auto mode. **À n'activer qu'après P0/P1** — inutile d'automatiser le triage tant que la couche
observée est fausse.

---

## 4. Stratégie modèle / tokens (patrons Fable 5)

Routing **par complexité estimée**, pas par rôle figé — la majorité des tokens passent au tarif
worker, et on n'escalade que ce qui le mérite. Le tier worker n'est pas verrouillé sur Sonnet :
on monte sur Opus quand la tâche est sensible, on redescend sur Sonnet quand elle est mécanique.

| Tier | Modèle | Pour quoi | Items du backlog |
|------|--------|-----------|------------------|
| Advisor / orchestrateur (rare, ~1 appel) | **Fable 5** | Décisions de correction délicates, arbitrages de contrat, design d'abstraction | Sémantique RxO Deadline (P1-3), design de la base partagée `_DdsObservabilityBase` (P2-1), arbitrage « câbler les octets CDR vs rétrograder la doc » (P0-2) |
| Worker complexe | **Opus 4.8** | Refactors protocol-sensibles, math à corriger avec soin, logique stateful | Extraction base + helpers (P0-1 / P2-1), fix fréquence + gaps `topic_metrics` (P1-1 / P1-2), bornage `LifecycleBuffer` (P1-4), `take_iter → read_iter` (P1-5) |
| Worker mécanique | **Sonnet 5** | Volumineux, déterministe, faible jugement | Cascade docs (P1-6), tests paramétrés une fois les helpers extraits (P0-1), collapse des stubs (P2-2), fixes mineurs (P2-5) |

Règle : **estimer la complexité avant de déléguer** et router en conséquence. En cas de doute, un
appel unique à l'advisor Fable 5 pour cadrer, puis exécution au tier worker approprié. Chaque
sous-agent garde son cache → les appels répétés ne repaient pas le contexte partagé.

- **Scripts > raisonnement** pour le déterministe : `make check`, le smoke-test mock-mode, le check
  de dérive de compte d'outils (comparer `MVP_TOOLS` aux tables README/doc) = un script, pas un
  raisonnement à chaque tour.
- **Piloter avant un grand run** : tout workflow multi-agents (ex. audit récurrent) se teste sur une
  tranche avant de fan-out.

---

## 5. Artefacts créés par cet audit

1. **`docs/projet-file/action-plan-audit-2026-07-08.md`** — ce document (hors sdist via
   `[tool.hatch.build.targets.sdist] exclude`).
2. **`.claude/skills/topicforge/verify-change/SKILL.md`** — skill de vérification audit-informé
   (local, gitignored `.claude/` par design CLAUDE.md §13).

Aucune modification de `src/` ni de tests dans le cadre de l'audit : les fixes ci-dessus sont du
backlog à valider par le mainteneur, pas des changements appliqués unilatéralement.

---

## 6. Découpage en lots (exécution par batch)

Chaque lot = une unité de travail cohérente, avec critère de sortie déterministe (`/goal`) et tier
modèle. Une branche `fix/lotN-...` par lot, un `make check` + skill `verify-change` vert avant merge.

### Lot 0 — Socle testabilité *(prérequis dur — bloque tout le reste)* ✅ FAIT (2026-07-08)

> Extraction faite vers `common/qos_normalize.py` + `common/dds_introspection.py`
> (behavior-preserving, alias back dans les adaptateurs). `pytest-cov` + `rosbags`
> ajoutés à `[dev]`, config coverage `fail_under=85`. 2 nouveaux fichiers de tests
> (modules extraits ~91-92 % couverts). Test bag I/O réel dé-skippé + réparé
> (drift API rosbags). **Baseline 399 → 457 passed, 24 → 23 skipped, ruff + cov verts.**

- **Contenu** : P0-3 (installer `pytest-cov` + seuil publié) · P0-1 (extraire les helpers purs
  `_cyclone/_fast_qos_to_profile` + `_extract_guid/_vendor_id/_hostname/_topic_name` vers
  `adapters/common/` + `tests/test_dds_qos_normalization.py`) · P2-4 (ajouter `rosbags` à l'extra CI).
- **Pourquoi groupé** : rien de DDS n'est vérifiable sans ça. Sortir la logique dans `common/` rend
  les fixes des lots suivants **single-homed et testés** (au lieu de dupliqués + aveugles).
- **Dépendance** : aucune. **Critère de sortie** : `pytest-cov` tourne, module extrait > 90 %,
  nouveaux tests verts, `make check` vert, nombre de skips inchangé.
- **Tier** : Opus 4.8 (extraction) + Sonnet 5 (tests, config CI). **Effort** : M.

### Lot 1 — Vérité documentaire *(indépendant, faible risque — insérable n'importe quand)* ✅ FAIT (2026-07-08)

> `product-plan.md` §1/§4 réalignés sur 11 outils ; §11 : décision de re-scope
> rétroactive ajoutée qui **ferme la barrière de gouvernance franchie** (plafond
> révisé à 11, un 12ᵉ outil exige une discussion documentée). Honnêteté C1 :
> README + description `peek_dds_samples` ne promettent plus le repli
> `_raw_bytes_hex` (vide sur le chemin user-topic raw). **476 passed, verts.**
> *Non touché* : `CLAUDE.md` (gitignored/local, §2 « MVP verrouillé » possiblement
> gelé volontairement) — laissé au mainteneur, signalé dans le rapport.

- **Contenu** : M6/P1-6 (aligner `product-plan.md` §1/4/11/13 + `CLAUDE.md` §2/12 sur les 11 outils,
  fermer la barrière de gouvernance franchie) · volet doc de C1/P0-2 (aucune description d'outil ni
  README ne promet un comportement live absent).
- **Dépendance** : aucune (l'arbitrage C1 « câbler vs rétrograder » = 1 appel advisor Fable 5).
- **Critère de sortie** : compte d'outils + version identiques dans README / product-plan / CLAUDE /
  CHANGELOG / `MVP_TOOLS` ; zéro promesse doc non tenue. **Tier** : Fable 5 (arbitrage) → Sonnet 5
  (cascade `docs-curator`). **Effort** : S.

### Lot 2 — Bugs fonctionnels metrics / QoS *(après Lot 0)* ✅ FAIT (2026-07-08)

> P1-1 fréquence : `(N-1)/(newest-oldest)` sur le span réel des samples (plus de
> division par `now-oldest`) ; snapshot au même timestamp → `None`. P1-2 gaps :
> comptés par writer (nouveau `MetricsSample.writer_guid`) + garde reset/wrap à
> 10 000. P1-3 Deadline : absent = infini → reader fini vs writer absent = incompatible.
> Tests rouge-puis-vert ajoutés. **457 → 464 passed, ruff + cov (88.83 %) verts.**

- **Contenu** : P1-1 (fréquence off-by-one + timestamps snapshot) · P1-2 (gaps par writer GUID +
  détection wrap/reset) · P1-3 (faux-négatif Deadline RxO offered-infinite).
- **Pourquoi groupé** : trois outils livrés qui renvoient des chiffres faux ; tous en logique pure
  désormais testable. **Critère de sortie** : chaque bug a un test rouge-puis-vert, math validée sur
  distribution connue. **Tier** : Opus 4.8 + Fable 5 (sémantique RxO Deadline). **Effort** : M.

### Lot 3 — Fiabilité runtime *(mémoire + lifecycle)* ✅ PARTIEL (2026-07-08)

> **Fait (pur, testé)** : P1-4 `LifecycleBuffer._participants` borné à
> `MAX_PARTICIPANTS=4096` (évince les tombstones `"left"` d'abord) ; docstring
> « bounded » désormais vrai. `MetricsBuffer._samples` borné à `MAX_TOPICS=4096`
> (P2-5). Tests d'éviction ajoutés. **464 → 470 passed, cov 88.90 %.**
> **Reporté au batch rig** : P1-5 (`take_iter → read_iter`, reader persistant,
> anti-flapping) — non vérifiable sans bus réel, ne pas modifier l'adaptateur à
> l'aveugle. À traiter avec le Lot 5 sur `scripts/integration/`.

- **Contenu** : P1-4 (borner `LifecycleBuffer._participants` + purge, corriger le docstring) · borner
  `MetricsBuffer._samples` (P2-5) · P1-5 (`take_iter → read_iter`, reader persistant, anti-flapping).
- **Critère de sortie** : cap testé, docstring « bounded » vrai, pas de flapping sur scénario simulé.
  ⚠️ **P1-5 non vérifiable sans bus réel** → valider sur le rig `scripts/integration/`.
- **Tier** : Opus 4.8. **Effort** : M.

### Lot 4 — Nettoyage mineur *(mécanique, indépendant)* ✅ FAIT (2026-07-08)

> OpenDDS `is_available()` → `False` (S1) · `__slots__` string (C2) · cap
> récursion `decode_field_value` (M6) · `_encode_raw_bytes` slice-avant-hex (M5)
> · test e2e `AdapterError → ToolError`/isError · test de cohérence vendor
> Literal (P2-3, pin — pas d'élargissement du contrat wire) · `_KNOWN_TOOLS`
> + `peek_bag_samples` · dead code retiré (bag_service). **470 → 476 passed, verts.**
> *Non fait (reporté)* : collapse des stubs OpenDDS/Dust (P2-2) — refactor à
> faible valeur, laissé pour plus tard.

- **Contenu** : P2-3 (Literal `vendor` aligné) · S1 (`OpenDdsAdapter.is_available()` → `False`) ·
  `__slots__` string (`cdr_decoder.py:94`) · `_KNOWN_TOOLS` + `peek_bag_samples` · test e2e
  `AdapterError → isError` · P2-2 (collapse stubs) · `_encode_raw_bytes` slice-avant-hex · cap
  profondeur récursion · dead code (`parse_echo_yaml`, `_ = (...)`).
- **Critère de sortie** : `make check` vert, chaque mineur adressé ou justifié. **Tier** : Sonnet 5.
  **Effort** : S-M.

### Lot 5 — Déduplication base DDS *(risqué — en dernier)* ✅ PRÉPARÉ (2026-07-08) — ⚠️ à valider sur le rig

> **Fait & testé (binding-free)** : logique de pairing `detect_qos_mismatches`
> extraite vers `common/qos_endpoints.py` (+ fix perf O(R*W) → profils writer
> pré-calculés) ; `validate_domain_id` partagé. Tests : `test_qos_endpoints.py`
> + validation domaine des stubs. **476 → 485 passed, cov 89.12 %.**
> **À valider sur le rig `scripts/integration/`** (non exécutable ici, bindings
> absents) : le wiring Cyclone/Fast de `detect_qos_mismatches` (validé ruff +
> py_compile seulement) et la bascule `take_iter → read_iter` (P1-5). Ne pas
> release avant un run réel-bus vert.
> *Non fait* : base-classe complète `_DdsObservabilityBase` (les 5 raisers
> ROS2 / participant_events / topic_metrics restent dupliqués, faible valeur,
> risque élevé sur code non testé) — la dedup à plus forte valeur (pairing QoS)
> est capturée.

- **Contenu** : P2-1 (`_DdsObservabilityBase` : validation domaine, 5 raisers ROS2, squelette
  `detect_qos_mismatches`, `participant_events`, `topic_metrics`, boucles metrics).
- **Pourquoi en dernier** : c'est le code le moins testable (adaptateurs à import-binding), donc le
  refactor le plus risqué ; le faire **après** que la logique soit extraite en `common/` (Lot 0)
  réduit sa surface. ⚠️ valider sur le rig intégration. **Tier** : Opus 4.8 + Fable 5 (design).
  **Effort** : M-L.

### Hors lots — DEFER
- **P2-6** (durcissement hosted : `Path.resolve()` + racine, rejet `-`-préfixé, env-scrub subprocess)
  reste **DEFER** tant que le déploiement est mono-tenant local — conforme au triage existant.

### Séquence recommandée

```
Lot 0  ──►  Lot 2  ──►  Lot 3  ──►  Lot 5
   │
   └─►  Lot 1  (indépendant, quand tu veux)
   └─►  Lot 4  (indépendant, quand tu veux)
```

Lot 0 d'abord (débloque). Lots 1 et 4 sont indépendants et peuvent s'insérer à tout moment. Lots 2 et
3 veulent Lot 0 fait. Lot 5 en dernier. Un lot par branche, mergé vert avant d'attaquer le suivant.
