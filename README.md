# CAD-RADS report generator

Génère des rapports de scanner coronaire (CCTA) synthétiques **paramétrables**, pour
la recherche sur les LLM. Aucune donnée patient, aucune dépendance externe
(Python 3.10+ standard library uniquement).

> **Statut** : plomberie, plan, moteur de classification et contrôles testés
> (65 tests, exécutés en CI). L'appel réel passe par le CLI `opencode` (voir
> « Brancher ton compte opencode ») ; il n'a pas pu être validé dans
> l'environnement de développement. Seul `backend: http_api` reste un placeholder.

---

## Démarrage rapide

Cinq étapes, dans l'ordre. Chacune indique ce que tu dois voir avant de passer à
la suivante.

### 1. Prérequis

Python 3.10+ et le CLI `opencode` connecté à ton compte. Aucun `pip install`
n'est nécessaire : le projet n'utilise que la bibliothèque standard
(`requirements.txt` documente cette absence).

```bash
python3 --version      # 3.10 ou plus
opencode auth list     # doit lister au moins un fournisseur
```

### 2. Se placer dans le projet

```bash
cd cadrads-generator        # le dossier qui contient run.py
```

Peu importe le nom de ton dossier local : c'est ici le dépôt Git tel que cloné.

### 3. Se connecter

```bash
python3 login.py
```

Choisis `1`. OpenCode ouvre son écran de connexion ; au retour, le script
enchaîne tout seul sur un appel de test réel.

**Attendu :** le message `Test REUSSI`, puis `Connexion validee`.
`run.py` refuse de démarrer sans cette étape — c'est voulu, pas un bug.

### 4. Vérifier le plan, sans dépenser d'appel

```bash
python3 run.py --plan-only
python3 run.py --show-prompt
```

**Attendu :** les 10 cas listés avec leur label calculé, et `derive cible/calcule : 0`.

### 5. Un cas réel, puis le corpus

```bash
python3 run.py --limit 1          # un seul appel
cat out/*/raw/SYN-0001.json       # prompt, réponse brute, contrôles
python3 run.py                    # tout ce que dit settings.json
```

**Attendu :** en fin d'exécution, les compteurs `label present`,
`modif. decrits`, `decouvr. decrites`.

### Les options en un coup d'œil

Sans aucune option, `run.py` exécute le pipeline complet : un appel au modèle par
cas, rapports dans `reports/`, manifeste et résumés.

| Option | Effet | Appel au modèle | Écrit |
|---|---|---|---|
| `--settings PATH` | utiliser un autre fichier de configuration | — | — |
| `--limit N` | ne traiter que les N premiers cas | selon le mode | selon le mode |
| `--export-prompts` | écrire aussi chaque prompt dans `prompts/` | oui | `prompts/*.md` + `index.tsv` |
| `--dry-run` | client factice, sorties isolées en `*-dryrun` | non | rapports factices |
| `--prompt-only` | écrire tous les prompts, sans générer | **non** | `prompts/*.md` + `index.tsv` |
| `--plan-only` | afficher le plan et les labels calculés | non | rien |
| `--show-prompt` | afficher le prompt du premier cas | non | rien |
| `--no-resume` | ignorer le manifeste existant | — | — |
| `--no-probe` | ne pas refaire le test de connexion au démarrage | — | — |
| `--quiet` | sortie minimale | — | — |

`--dry-run`, `--plan-only`, `--show-prompt` et `--prompt-only` sont **exclusifs
entre eux** : argparse refuse les combinaisons et le dit explicitement.
`--export-prompts` est composable avec un run normal ou un `--dry-run`.

`python3 run.py --help` liste la même chose, avec les valeurs par défaut.

`--prompt-only` est le moyen de relire les 2 000 prompts **avant** de dépenser
2 000 appels :

```bash
python3 run.py --prompt-only
ls out/*/prompts/ | head
cat out/*/prompts/index.tsv
```

### Si ça ne démarre pas

| Message | Quoi faire |
|---|---|
| `[LOGIN] Aucune connexion validée` | lance `python3 login.py` |
| `[LOGIN] ... validée avec une autre configuration` | tu as changé `llm.command` → relance `login.py` |
| `commande introuvable : opencode` | `which opencode` dans ce même terminal, sinon corrige `llm.command` |
| `[CONFIG] settings.json invalide` | le message liste les clés fautives et les valeurs admises |
| sortie du modèle sale (préambule, logs) | voir la table du point 4 de « Brancher ton compte opencode » |

**Le seul fichier de réglage est `settings.json`.** `prompts/` et `reference/`
sont aussi éditables, mais rien d'obligatoire pour démarrer.

## Structure du projet

```
cadrads-generator/            (racine du dépôt)
├── settings.json          ← config : le seul fichier que tu modifies
├── login.py               ← connexion opencode + test réel (avant run.py)
├── run.py                 ← point d'entrée de la génération
├── prompts/               system.md + user.md, éditables (versionnés à chaque run)
├── reference/
│   ├── cadrads_2_0.json           règles CAD-RADS (documentation)
│   └── incidental_findings.json   catalogue des découvertes fortuites
├── src/
│   ├── config.py          chargement + validation de settings.json
│   ├── planner.py         tirage stratifié et scénarios cliniques
│   ├── classify.py        calcul déterministe du label CAD-RADS
│   ├── incidental.py      tirage des découvertes fortuites
│   ├── prompt.py          rendu des prompts
│   ├── llm_client.py      appel du modèle (CLI opencode ; http_api = placeholder)
│   ├── auth.py            connexion opencode + test d'appel
│   ├── validate.py        nettoyage et contrôles automatiques
│   └── store.py           manifeste, reprise, export
└── tests/                 65 tests, stdlib uniquement
```

Les fichiers de `src/` ne sont pas faits pour être modifiés à la main : tout le
réglage passe par `settings.json`, `prompts/` et `reference/`.

---

## Réglages : settings.json

Tout se règle ici. Une clé inconnue est ignorée ; une valeur invalide arrête le
programme avec un message qui liste les valeurs admises.

| Groupe | Clé | Défaut | Effet |
|---|---|---|---|
| `run` | `n_reports` | `10` | nombre de rapports à générer |
| `run` | `run_id` | `"auto"` | `"auto"` = dossier neuf à chaque lancement ; une valeur fixe permet la reprise |
| `run` | `seed` | `42` | graine du tirage : même seed ⇒ même plan |
| `run` | `resume` | `true` | saute les cas déjà réussis |
| `run` | `max_consecutive_errors` | `5` | arrête tout si le modèle ne répond plus |
| `llm` | `backend` | `"opencode"` | `opencode`, `dry_run` ou `http_api` |
| `llm` | `command` | `["opencode","run","--model",…,"{prompt}"]` | commande exacte ; `{prompt}` est remplacé |
| `llm` | `model_label` | `"opencode/grok-code"` | ce qui est écrit dans la provenance — mets version + date |
| `llm` | `timeout_s` / `retries` / `delay_between_calls_s` | `240` / `2` / `1.5` | robustesse et débit |
| `generation` | `languages` | `["en"]` | `en`, `fr`, `de`, `it` |
| `generation` | `style` | `"random"` | `free`, `structured`, `random` |
| `generation` | `cadrads_weights` | voir fichier | distribution visée des catégories |
| `generation` | `modifier_weights` | voir fichier | fréquence de S, G, E, V, N |
| `generation` | `age_range` / `sex` / `indication` | `[45,88]` / `random` / `stable` | population simulée |
| `other_structures` | `describe_normal` | `false` | décrire les organes du champ de vue |
| `other_structures` | `include_incidental` | `false` | ajouter des découvertes fortuites mineures |
| `other_structures` | `prevalence_scale` / `max_per_case` | `1.0` / `3` | dosage et plafond des découvertes |
| `checks` | `label_integrity` / `modifier_mention` / `incidental_mention` | `fail` / `warn` / `warn` | `fail` exclut, `warn` signale, `off` ignore |
| `checks` | `min_words` / `max_words` | `35` / `800` | longueur acceptée |
| `export` | `filename` | `"{case_id}_{cadrads}_{lang}"` | nom des `.txt` |

Chaque clé porte aussi son explication dans le fichier (`_doc`), donc
`settings.json` reste lisible sans revenir ici.

---

## Le label CAD-RADS est calculé, jamais généré

`src/classify.py` implémente les règles de CAD-RADS 2.0 et **calcule** le label à
partir des lésions. Ce label est ensuite **imposé** au LLM dans le prompt
(« Reproduce this classification verbatim »). Conséquence : le label et les
lésions du prompt ne peuvent pas être incohérents.

Règles appliquées, par ordre de priorité (sur la sténose la plus sévère) :

| # | Condition | Catégorie |
|---|---|---|
| 1 | segment non diagnostique **et** aucune sténose ≥ 50 % | `N` |
| 2 | occlusion totale (100 %) | `5` |
| 3 | tronc commun ≥ 50 % **ou** 3 vaisseaux ≥ 70 % | `4B` |
| 4 | ≥ 70 % (1 ou 2 vaisseaux) | `4A` |
| 5 | 50–69 % | `3` |
| 6 | 25–49 % | `2` |
| 7 | 1–24 % | `1` |
| 8 | aucune plaque | `0` |

Le label final est composé dans l'ordre `CAD-RADS <catégorie>/<P>/<modificateurs>`,
modificateurs triés `N, S, G, E, V` (ex. `CAD-RADS 3/P2/N/S`). Les modificateurs
sans objet sont écartés (pas de stent en CAD-RADS 0, pas de `N` sans segment non
diagnostique) et la décision est tracée dans `classification_trace`.

**Distribution garantie.** La catégorie visée sert à générer un scénario plausible,
puis la catégorie est *recalculée* depuis les lésions. Si les deux divergent, la
valeur calculée est retenue et le cas est marqué `target_ok: false`. Sur 2 000 cas
avec les poids par défaut : **0 dérive**, distribution exacte.

---

## Autres structures et découvertes fortuites

Deux options **indépendantes, désactivées par défaut**, dans `settings.json >
other_structures` :

| Option | Effet | Change |
|---|---|---|
| `describe_normal` | le rapport décrit poumons, plèvre, médiastin, aorte, péricarde **normaux** | la forme du rapport |
| `include_incidental` | ajoute des anomalies **mineures** tirées à leur prévalence approximative | le contenu |

Ce sont bien deux choses différentes : décrire des organes sains (rapport complet)
n'est pas la même chose qu'ajouter des trouvailles fortuites.

### Le catalogue

`reference/incidental_findings.json` contient 14 découvertes (aorte, péricarde,
valves, ventricule, poumons, plèvre, rachis, abdomen supérieur), chacune avec :

```json
{ "id": "aortic_calcification", "organ": "aorta",
  "label_en": "atherosclerotic calcification of the thoracic aorta",
  "prevalence": 0.30, "age_min": 60, "sex": "any" }
```

Le tirage applique `prevalence × prevalence_scale`, ignore la découverte si
`age < age_min` ou si le sexe ne correspond pas, et plafonne à `max_per_case`
(3 par défaut). Résultat mesuré sur 3 000 cas : prevalence observée **conforme au
catalogue** (± 0,5 point), 34 % des patients sans aucune découverte, 1,07 en
moyenne par patient.

> **Cadrage** : ce sont des ordres de grandeur assumés comme **paramètres de
> conception**, pas une épidémiologie mesurée. Ils sont livrés avec le code,
> versionnés à chaque exécution et modifiables ; `prevalence_scale` permet une
> analyse de sensibilité sans rien recoder.

> **Effet du plafond** : avec `max_per_case: 2`, les découvertes fréquentes
> chutent de ~12 % (le plafond tronque). À 3, l'écart disparaît. Si tu veux des
> prévalences exactes, monte le plafond.

### Dans le rapport

Les découvertes sont listées séparément dans le prompt et le modèle doit les
décrire, en les plaçant après les coronaires et avant l'impression. Un contrôle
vérifie qu'elles sont **effectivement évoquées** (voir tableau ci-dessous).

---

## Brancher ton compte opencode

### 0. En deux mots

```bash
python3 login.py     # une fois, puis quand tu changes de compte ou de modèle
python3 run.py
```

`login.py` affiche l'état de connexion, te laisse te connecter (compte) ou coller
une clé API, puis fait **un vrai appel de test**. `run.py` **refuse de tourner**
tant que ce test n'a pas réussi, et refait un test court au démarrage (1 appel)
pour détecter un compte expiré entre deux lancements. `--no-probe` saute ce test.

Le feu vert est aussi **lié à la configuration** : si tu changes `llm.command`
(modèle, fournisseur), l'état précédent est considéré comme périmé et `run.py`
redemande un `login.py`. Un « validé » obtenu avec un modèle ne vaut rien pour un autre.

Ce que contient `.state/login.json` : uniquement le *résultat* du test (date,
modèle, latence, ok/échec). **Jamais de clé.** Le fichier est jetable : le
supprimer oblige simplement à repasser par `login.py`.

### Le principe : deux couches

Deux couches bien séparées — c'est ce qui protège tes clés :

1. **OpenCode gère l'authentification.** Tu te connectes une fois, les
   identifiants vont dans `~/.local/share/opencode/auth.json`. Le projet ne voit
   jamais la clé.
2. **Le projet ne fait que lancer une commande.** `settings.json > llm.command`
   est le template exact de cette commande.

> Ne mets **jamais** de clé dans `settings.json` : ce fichier est recopié dans
> `out/<run_id>/settings.snapshot.json` à chaque exécution. La clé finirait
> archivée dans les sorties du dataset.

### 1. Connecter le compte

```bash
opencode auth login       # choisir le provider, coller la clé s'il la demande
opencode auth list        # doit lister le provider connecté
opencode models           # ids exacts, format provider/model
opencode models --refresh # si un modèle vient d'être ajouté
```

### 2. Récupérer l'identifiant exact du modèle

`opencode models` renvoie des lignes `provider/model` (ex. `opencode/grok-code`).
C'est cette chaîne, et rien d'autre, qu'il faut mettre dans `--model`.

### 3. Écrire la commande

```json
"llm": {
  "backend": "opencode",
  "command": ["opencode", "run", "--model", "PROVIDER/MODELE", "{prompt}"],
  "model_label": "PROVIDER/MODELE@2026-10-31"
}
```

Variante recommandée : isoler l'agent dans un dossier vide, pour qu'il ne parte
pas explorer le dépôt pendant qu'il rédige un rapport.

```json
"command": ["opencode", "run", "--model", "PROVIDER/MODELE",
            "--dir", "/tmp/cadrads-scratch", "{prompt}"]
```

```bash
mkdir -p /tmp/cadrads-scratch   # une seule fois
```

`{prompt}` est remplacé par le prompt complet, en **un seul argument** (pas de
shell, pas d'échappement à gérer). Le prompt fait ~2 Ko : très loin de la limite
`ARG_MAX`. Le repli par stdin de `src/llm_client.py` ne s'applique pas ici, car
`opencode run` attend le message en argument positionnel.

### 4. Tester dans le shell avant de lancer le pipeline

```bash
opencode run --model PROVIDER/MODELE --dir /tmp/cadrads-scratch \
  "Write a 3-line synthetic coronary CTA report ending with the line CAD-RADS 0"

opencode stats --days 1     # tokens et coût de l'essai, pour extrapoler 2000 appels
```

Regarde si la sortie contient **uniquement** le rapport. Si l'agent ajoute un
préambule, des logs ou un bloc de code, trois options :

| Symptôme | Correctif |
|---|---|
| préambule / « Here is the report » | déjà géré par `clean()` dans `src/validate.py` |
| blocs de code (triple backtick) | déjà géré par `clean()` |
| logs ou événements mêlés | ajouter `--format json` puis parser les événements (à coder dans `llm_client.py`) |
| lenteur au démarrage | `opencode serve` une fois, puis ajouter `--attach http://localhost:4096` |

### 5. Puis dans le pipeline

```bash
python3 run.py --show-prompt     # copier-coller ce prompt dans la commande du point 4
python3 run.py --limit 1         # 1 vrai appel
cat out/*/raw/SYN-0001.json      # vérifier prompt + réponse brute
python3 run.py --limit 10        # l'échantillon de contrôle
```

### Choisir une clé API plutôt que le compte

Deux routes, sans rien changer au projet :

- `opencode auth login --provider <id>` : la clé va dans `auth.json`, utilisée
  automatiquement ensuite ;
- variables d'environnement du provider (ex. `ANTHROPIC_API_KEY`), qu'OpenCode
  lit au démarrage, y compris depuis un `.env` du dossier courant.

`backend: "http_api"` reste un placeholder vide dans `src/llm_client.py`
(classe `HttpApiClient`) si tu veux un appel HTTP direct plus tard.

## Ce qui est produit

```
out/<run_id>/
├── reports/              .txt acceptés (le dataset)
├── prompts/              prompts rendus (avec --export-prompts ou --prompt-only)
│                         ├── SYN-0001.md  (le prompt exact, rien d'ajouté)
│                         └── index.tsv    case_id, label, langue, style, sha
├── reports_rejected/     .txt exclus par un contrôle automatique
├── raw/<case_id>.json    prompt complet + réponse brute + contrôles + trace
├── manifest.jsonl        une ligne JSON par tentative (registre + audit)
├── reports_index.csv     tableau prêt à ouvrir
├── summary.json          comptages par strate, contrôles, dérive du plan
├── run.json              identifiant + empreinte de config (garde-fou)
├── settings.snapshot.json  config exacte utilisée
├── prompts.snapshot.json   prompts exacts utilisés
└── PROVENANCE.txt        horodatage, empreinte de config, modèle
```

En `--dry-run`, le `run_id` est suffixé `-dryrun` et le manifeste porte
`dry_run: true` : les sorties factices ne peuvent pas contaminer un vrai run.

### À quoi ressemble un rapport

Chaque `.txt` est un rapport complet, terminé par le label imposé :

```
Indication: evaluation of coronary artery disease in a 67-year-old patient.
Technique: ECG-gated coronary CT angiography, 0.5 mm slices.
Findings: non-calcified plaque of the proximal LAD with 82% luminal stenosis,
and a patent stent in the mid RCA.
Impression: severe stenosis of the proximal LAD.
CAD-RADS 4A/P2/S
```

*Exemple de forme : la rédaction exacte dépend du modèle et du style demandé
(libre ou structuré).*

### À quoi ressemble une ligne de manifeste

```json
{"case_id": "SYN-0001", "status": "ok", "file": "reports/SYN-0001_4A_en.txt",
 "expected": {"cadrads": "4A", "label": "CAD-RADS 4A/P2/S", "language": "en"},
 "checks": {"ok": true, "results": {"label": {"ok": true, "category_found": "4A"}}},
 "meta": {"attempts": 1, "latency_s": 6.4, "model": "provider/modele@2026-10-31",
          "prompt_sha": "1c8cb9d454eb41fb", "dry_run": false},
 "timestamp": "2026-10-31T12:05:00"}
```

`status` vaut `ok`, `excluded` (rejeté par un contrôle automatique) ou `error`
(échec technique). Ce fichier est la source de vérité pour la reprise et l'audit :
c'est lui qui permet de republier un corpus sans le régénérer.

---

## Reprise

Le manifeste sert de registre : `resume: true` saute les cas déjà réussis et
**retente** ceux qui ont échoué. `run.max_consecutive_errors` interrompt le run si
l'API est cassée, plutôt que de brûler 2 000 appels.

**Reprendre suppose un `run_id` explicite.**

| `run.run_id` | Comportement |
|---|---|
| `"auto"` (défaut) | **nouveau dossier à chaque lancement** (horodatage + suffixe aléatoire) → la reprise ne s'applique pas |
| `"etude-1"` | dossier fixe → la reprise fonctionne |

Le suffixe aléatoire évite que deux lancements dans la même seconde écrasent le
même dossier. Pour une collecte de 2 000 rapports, **fixe `run_id`** dès le départ.

**Garde-fou de provenance** : un dossier de run ne peut pas être réutilisé avec une
autre configuration. Le `settings_fingerprint` est comparé à `run.json` ; en cas
d'écart le programme refuse (exit 2) au lieu de produire un snapshot mensonger.

---

## Contrôles automatiques

| Contrôle | Mesure | Réglage |
|---|---|---|
| `label` | le label calculé est présent, verbatim | `checks.label_integrity` |
| `modifiers` | stent / pontage / segment non diagnostique sont-ils **décrits dans le corps** (pas seulement dans le label) | `checks.modifier_mention` |
| `incidental` | chaque découverte fortuite est évoquée dans le corps (correspondance par organe) | `checks.incidental_mention` |
| `language` | langue détectée == langue demandée | `checks.language_match` |
| `length` | nombre de mots dans l'intervalle | `checks.min_words` / `max_words` |
| `forbidden` | pas de « as an AI », de bloc de code, de « language model » | `checks.forbidden_phrases` |

`fail` ⇒ rapport déplacé dans `reports_rejected/`. `warn` ⇒ conservé + drapeau.
Les taux sont agrégés dans `summary.json`.

Ces contrôles sont **lexicaux**. Un rapport peut tous les passer et rester
cliniquement incohérent : c'est précisément le rôle des 2 lecteurs experts.

---

## Tests

```bash
python3 tests/test_classify.py     # 18 tests  — règles CAD-RADS
python3 tests/test_incidental.py   # 16 tests  — prévalences, gating, plafond
python3 tests/test_validate.py     # 16 tests  — nettoyage et contrôles
python3 tests/test_auth.py         #  8 tests  — état de connexion, chemin d'échec
python3 tests/test_cli.py          #  7 tests  — flags, exclusivité, export, doc à jour
```

Couvrent : seuils de sténose, occlusion, tronc commun, 3 vaisseaux, précédence
`5 > 4B`, cas non diagnostique, filtrage des modificateurs, cohérence du plaque
burden, format du label ; prévalences conformes au catalogue, respect de
`age_min` / `sex`, plafond, exclusion, déterminisme ; nettoyage des préambules et
blocs de code, tolérance du label, mention des modificateurs et des découvertes.

65 tests, stdlib uniquement. Ils tournent aussi en intégration continue
(`.github/workflows/tests.yml`, Python 3.10 à 3.14) : c'est la preuve que le label
imposé est conforme et que le tirage est reproductible.

---

## Reproducibilité

- Tirage déterministe : même `run.seed` ⇒ même plan (catégories, âges, vaisseaux, labels).
- `settings.snapshot.json` + `prompts.snapshot.json` + `PROVENANCE.txt` archivés à chaque run.
- `raw/` conserve la réponse brute et la trace de classification.
- **Figer** le nom de modèle avec sa version et la date dans `model_label`
  (ex. `grok-code@2026-10-31`) : un modèle mis à jour silencieusement rend le
  dataset non reproductible.

---

## Avant une grosse collecte

1. Les quatre suites de tests passent.
2. `llm.model_label` porte la **version et la date** du modèle.
3. Trois rapports relus avec `--limit 3` avant de lancer 2 000 appels.
4. `run_id` fixé dans `settings.json`, sinon aucune reprise n'est possible.

## Limites de l'outil

- **Précédence `5 > 4B`** : lorsqu'une occlusion et une atteinte du tronc commun
  coexistent, la catégorie la plus élevée est retenue. C'est une convention, à
  changer en une ligne dans `classify_category` si tu préfères l'inverse.
- **Plaque burden semi-quantitatif** : le consensus ne fournit pas de formule
  fermée ; ici `P` est contraint par la catégorie et par le nombre de vaisseaux
  atteints, pas calculé par une équation.
- **Anatomie simplifiée** : pas de dominance, de collatérales, de score calcique
  segmentaire, ni de FFR-CT.
- **Découvertes fortuites mineures uniquement** : aucune masse, lymphadénopathie,
  embolie pulmonaire ni anévrisme significatif. Le corpus est donc plus propre
  qu'une vraie cohorte.
- **Contrôles automatiques lexicaux** : un rapport peut tous les passer et rester
  cliniquement incohérent.
- **Homogénéité du corpus non mesurée** par l'outil (post-traitement : distinct-n,
  templates, clusters d'embeddings).
- **Usage recherche uniquement.** Ces rapports ne sont pas des rapports médicaux.

---

## Licence

MIT — voir [LICENSE](LICENSE). Le texte inclut un avertissement médical : ce
logiciel produit des rapports synthétiques à des fins de recherche, pas des
rapports médicaux.

Si ton institution impose Apache-2.0 ou une autre licence, remplace simplement le
fichier `LICENSE` et le champ `license` de `CITATION.cff`.

## Citer ce travail

`CITATION.cff` fournit les métadonnées (GitHub affiche alors un bouton
« Cite this repository »). Deux champs sont à corriger avant la publication :

- `repository-code` : l'URL réelle du dépôt ;
- `date-released`.

Une fois l'article publié, ajouter la référence à la section `preferred-citation`.

## Données et disponibilité du code

- Le **code** est publié ici. Il suffit à régénérer le corpus : le plan est
  déterministe (`run.seed`), les prompts sont versionnés dans chaque run.
- Le **dataset** généré (rapports `.txt` + `manifest.jsonl` + `summary.json`) n'a
  pas sa place dans ce dépôt : il se dépose sur Zenodo avec un DOI, et se cite
  via le bloc `preferred-citation` de `CITATION.cff`.
- Les **prompts exacts** utilisés sont archivés dans chaque `out/<run_id>/`, donc
  un corpus publié reste auditable même si `prompts/` évolue ensuite.
