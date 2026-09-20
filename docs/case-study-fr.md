# Agent IA de tri des e-mails entrants

**Un agent autonome qui lit les e-mails entrants, les classe et crée une fiche structurée
dans Notion — sans intervention humaine.**

---

## Le problème

Dans une PME industrielle, tout arrive par la même boîte mail : une ligne de production à
l'arrêt, une demande de devis, une facture contestée, une échéance URSSAF et une dizaine de
newsletters. Quelqu'un — souvent la personne qui a le moins de temps — trie tout cela à la
main et ressaisit l'essentiel dans l'outil de l'entreprise.

Deux choses se passent mal. Les demandes urgentes restent non lues derrière les
newsletters. Et les demandes traitées oralement ne laissent aucune trace : impossible de
dire, trois mois plus tard, combien de réclamations sont arrivées ni en combien de temps un
devis a été envoyé.

## La solution

Un agent qui relève la boîte mail, comprend chaque message et crée une fiche dans l'outil de
l'entreprise. Chaque fiche porte une catégorie, une priorité, un résumé en une ligne,
l'échéance mentionnée dans le message, la langue détectée et le texte original de l'e-mail.

Cinq catégories : support technique, demande commerciale, réclamation, administratif, autre.
Quatre niveaux de priorité, attribués selon la conséquence et le délai — une production à
l'arrêt ou un ultimatum à 48 heures est urgent ; une échéance légale à moins de quinze jours
est élevée ; un expéditeur qui précise lui-même que ce n'est pas bloquant reste en priorité
basse, même s'il signale un dysfonctionnement.

## Fonctionnement

```
Boîte mail (Gmail API / IMAP) → Agent IA (Python) → Base Notion
                                       ↓
                                  API Claude
```

L'agent lit les messages non lus, soumet chacun au modèle et écrit le résultat. Un passage
sur dix-huit messages prend environ deux minutes et coûte quelques centimes.

L'étape de classification s'appuie sur un **schéma imposé** : le modèle reçoit une structure
JSON et ne peut répondre qu'en la remplissant. Il ne peut pas produire de texte libre, et
toute valeur hors des listes autorisées est rejetée avant d'atteindre Notion. C'est ce qui
sépare une démonstration d'un outil sur lequel une entreprise peut s'appuyer.

## Résultats

Mesurés sur dix-huit e-mails annotés à la main, en français et en anglais, couvrant les cinq
catégories :

| Indicateur | Résultat |
|---|---|
| Exactitude de la catégorie | 18/18 (100 %) |
| Détection des échéances | 18/18 (100 %) |
| Priorité, correspondance exacte | 14/18 (78 %) |
| Priorité, à un niveau près | 17/18 (94 %) |

La priorité relève du jugement, pas du fait : trois des quatre écarts portent sur un seul
niveau, sur des messages où deux personnes seraient elles aussi en désaccord. Le rapport
d'exactitude est livré avec le projet et peut être relancé sur n'importe quel jeu
d'exemples — y compris celui du client, ce qui reste la bonne façon de valider le système
avant toute mise en service.

## Fiabilité

En cours de projet, Notion a publié une nouvelle version de son API qui a déplacé les
propriétés des bases dans un nouveau modèle de données et supprimé un point d'entrée utilisé
par l'intégration. Celle-ci a cessé de fonctionner.

Aucun e-mail n'a été perdu. Les écritures en échec sont parties dans une file rejouable, les
messages sont restés non lus dans la boîte, et une fois la version de l'API et celle du SDK
figées, l'ensemble du lot a été retraité sans aucune ressaisie.

Ce comportement est voulu :

- Un message n'est marqué comme lu **qu'après** confirmation de l'écriture de la fiche. Une
  interruption en cours de traitement se rejoue au lieu de se perdre.
- Chaque échec est consigné dans une file que l'on vide par une seule commande.
- Les doublons sont impossibles : chaque fiche porte l'identifiant unique de l'e-mail, et
  l'agent vérifie avant de créer.
- La version de l'API et celle du SDK sont figées, afin qu'une refonte côté fournisseur ne
  puisse pas interrompre silencieusement une installation en production.

## Technologies

Python · API Claude (schéma imposé) · Gmail API (OAuth), IMAP en alternative · API Notion ·
quatre dépendances au total.

La boîte mail et la destination sont deux modules séparés derrière une interface commune.
Passer de Gmail à IMAP tient en une ligne de configuration ; remplacer Notion par Airtable,
HubSpot, Odoo ou une table interne revient à réécrire un seul fichier.

## Sécurité

La démonstration tourne sur une boîte dédiée, remplie d'e-mails fictifs — aucune donnée
personnelle de tiers. Les autorisations OAuth sont réduites au strict nécessaire : l'agent
peut lire les messages et les marquer comme lus, mais **ne peut pas en envoyer**. Les clés
sont stockées dans des variables d'environnement, jamais dans le dépôt. L'agent ne lit qu'un
libellé dédié, pas l'ensemble de la boîte.

## Adapter l'outil à votre entreprise

Le fonctionnement reste le même quelle que soit la destination. Ce qui change d'un client à
l'autre : la liste des catégories, les règles de priorité et l'outil dans lequel les fiches
atterrissent. Ces trois éléments relèvent du paramétrage, pas d'un développement.

La façon honnête de commencer est une mesure : cinquante de vos propres e-mails, annotés par
quelqu'un qui connaît le métier, passés dans le classificateur. Vous obtenez un chiffre réel
sur votre courrier, pas sur le mien, et cela coûte une demi-journée. Si le résultat est bon,
l'intégration représente quelques jours. Sinon, vous aurez perdu une demi-journée plutôt
qu'un projet.
