----------------------------- MODULE Campaign -----------------------------
EXTENDS Naturals, FiniteSets, Sequences, TLC

(***************************************************************************
This is the bounded lifecycle abstraction of campaign_engine.reducer.  It
models one immutable approved campaign, a finite dependency graph, one
implementation generation, at most one validation correction, one review
cohort, one combined repair, one closure review, finite publication effects,
durable autonomous-operation rank, named waiting events, and terminal STOP.

The Python reducer remains the executable lifecycle authority.  The
CoreReducerNodeTransitions relation below is intentionally machine-checked by
tests/test_campaign_formal_conformance.py against real reducer executions.
***************************************************************************)

CONSTANTS Nodes, Dependencies, TopoRank, InitialBudget, MaxRevision,
          CampaignId, SpecificationRevision, SpecificationDigest,
          InitialGraphDigest, RequiredPublicationEffects

(***************************************************************************
These definitions are the bounded TLC model values selected by Campaign.cfg.
Keeping function construction in the module avoids relying on unsupported
TLC configuration expressions while leaving the lifecycle constants generic.
***************************************************************************)
ModelNodes == {"n1"}
ModelDependencies == [n \in ModelNodes |-> {}]
ModelTopoRank == [n \in ModelNodes |-> 0]

ASSUME /\ Nodes # {}
       /\ IsFiniteSet(Nodes)
       /\ Dependencies \in [Nodes -> SUBSET Nodes]
       /\ TopoRank \in [Nodes -> Nat]
       /\ InitialBudget \in Nat \ {0}
       /\ MaxRevision \in Nat \ {0}
       /\ RequiredPublicationEffects \in Nat \ {0}

CampaignStates == {
  "DRAFT", "APPROVED", "RUNNING", "WAITING_EXTERNAL", "WAITING_HUMAN",
  "COMPLETED", "FAILED", "CANCELLED"
}

NodeStates == {
  "PENDING", "ADMITTED", "IMPLEMENTING", "VALIDATING", "CANDIDATE_FROZEN",
  "CHECKS_AND_REVIEW", "FINDINGS_FROZEN", "REPAIR_AUTHORIZED", "REPAIRING",
  "REVALIDATING", "CLOSURE", "READY_TO_PUBLISH", "PUBLISHING", "DONE",
  "FAILED_EXACT_NODE", "CANCELLED"
}

WaitingCampaignStates == {"WAITING_EXTERNAL", "WAITING_HUMAN"}
TerminalCampaignStates == {"COMPLETED", "FAILED", "CANCELLED"}
TerminalNodeStates == {"DONE", "FAILED_EXACT_NODE", "CANCELLED"}

(***************************************************************************
The required node edges are data as well as behavior.  Python tests parse
this relation and produce every edge through reduce(snapshot, event).
***************************************************************************)
CoreReducerNodeTransitions == {
  <<"ADMIT_NODE", "PENDING", "ADMITTED">>,
  <<"START_IMPLEMENTATION", "ADMITTED", "IMPLEMENTING">>,
  <<"IMPLEMENTATION_COMPLETED", "IMPLEMENTING", "VALIDATING">>,
  <<"REQUEST_VALIDATION_CORRECTION", "VALIDATING", "IMPLEMENTING">>,
  <<"VALIDATION_PASSED", "VALIDATING", "CANDIDATE_FROZEN">>,
  <<"VALIDATION_FAILED", "VALIDATING", "FAILED_EXACT_NODE">>,
  <<"START_REVIEW", "CANDIDATE_FROZEN", "CHECKS_AND_REVIEW">>,
  <<"FREEZE_FINDINGS", "CHECKS_AND_REVIEW", "FINDINGS_FROZEN">>,
  <<"MARK_READY_TO_PUBLISH", "FINDINGS_FROZEN", "READY_TO_PUBLISH">>,
  <<"AUTHORIZE_REPAIR", "FINDINGS_FROZEN", "REPAIR_AUTHORIZED">>,
  <<"START_REPAIR", "REPAIR_AUTHORIZED", "REPAIRING">>,
  <<"REPAIR_COMPLETED", "REPAIRING", "REVALIDATING">>,
  <<"REVALIDATION_PASSED", "REVALIDATING", "CLOSURE">>,
  <<"REVALIDATION_FAILED", "REVALIDATING", "FAILED_EXACT_NODE">>,
  <<"COMPLETE_CLOSURE", "CLOSURE", "READY_TO_PUBLISH">>,
  <<"COMPLETE_CLOSURE", "CLOSURE", "FAILED_EXACT_NODE">>,
  <<"START_PUBLISH", "READY_TO_PUBLISH", "PUBLISHING">>,
  <<"PUBLISH_CONFIRMED", "PUBLISHING", "READY_TO_PUBLISH">>,
  <<"PUBLISH_CONFIRMED", "PUBLISHING", "DONE">>,
  <<"PUBLISH_FAILED", "PUBLISHING", "FAILED_EXACT_NODE">>
}

VARIABLES campaign, nodeState, revision, authorityEpoch, cancellationEpoch,
          rankRemaining, budgetReceipts, implementationUsed,
          validationCorrectionUsed, reviewUsed, repairUsed, closureUsed,
          blockersFrozen, publicationRemaining, contractCampaignId,
          contractSpecRevision, contractSpecDigest, graphDigest,
          campaignGeneration

vars == <<campaign, nodeState, revision, authorityEpoch, cancellationEpoch,
          rankRemaining, budgetReceipts, implementationUsed,
          validationCorrectionUsed, reviewUsed, repairUsed, closureUsed,
          blockersFrozen, publicationRemaining, contractCampaignId,
          contractSpecRevision, contractSpecDigest, graphDigest,
          campaignGeneration>>

contract == <<contractCampaignId, contractSpecRevision, contractSpecDigest,
              graphDigest, campaignGeneration>>

usage == <<implementationUsed, validationCorrectionUsed, reviewUsed,
           repairUsed, closureUsed>>

KeepContract == UNCHANGED contract
KeepBudget == UNCHANGED <<rankRemaining, budgetReceipts>>
KeepUsage == UNCHANGED usage

SpendBudget ==
  /\ rankRemaining > 0
  /\ rankRemaining' = rankRemaining - 1
  /\ budgetReceipts' = budgetReceipts + 1

Running == campaign = "RUNNING"
DependenciesDone(n) == \A d \in Dependencies[n] : nodeState[d] = "DONE"

Init ==
  /\ campaign = "DRAFT"
  /\ nodeState = [n \in Nodes |-> "PENDING"]
  /\ revision = 0
  /\ authorityEpoch = 0
  /\ cancellationEpoch = 0
  /\ rankRemaining = InitialBudget
  /\ budgetReceipts = 0
  /\ implementationUsed = [n \in Nodes |-> FALSE]
  /\ validationCorrectionUsed = [n \in Nodes |-> FALSE]
  /\ reviewUsed = [n \in Nodes |-> FALSE]
  /\ repairUsed = [n \in Nodes |-> FALSE]
  /\ closureUsed = [n \in Nodes |-> FALSE]
  /\ blockersFrozen = [n \in Nodes |-> FALSE]
  /\ publicationRemaining = [n \in Nodes |-> RequiredPublicationEffects]
  /\ contractCampaignId = CampaignId
  /\ contractSpecRevision = SpecificationRevision
  /\ contractSpecDigest = SpecificationDigest
  /\ graphDigest = InitialGraphDigest
  /\ campaignGeneration = 1

Approve ==
  /\ campaign = "DRAFT"
  /\ campaign' = "APPROVED"
  /\ revision' = revision + 1
  /\ UNCHANGED <<nodeState, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

Start ==
  /\ campaign = "APPROVED"
  /\ campaign' = "RUNNING"
  /\ revision' = revision + 1
  /\ UNCHANGED <<nodeState, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

WaitExternal ==
  /\ Running
  /\ campaign' = "WAITING_EXTERNAL"
  /\ revision' = revision + 1
  /\ UNCHANGED <<nodeState, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

WaitHuman ==
  /\ Running
  /\ campaign' = "WAITING_HUMAN"
  /\ revision' = revision + 1
  /\ UNCHANGED <<nodeState, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

Resume ==
  /\ campaign \in WaitingCampaignStates
  /\ campaign' = "RUNNING"
  /\ revision' = revision + 1
  /\ UNCHANGED <<nodeState, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

AdvanceAuthority ==
  /\ campaign = "WAITING_HUMAN"
  /\ campaign' = campaign
  /\ authorityEpoch' = authorityEpoch + 1
  /\ revision' = revision + 1
  /\ UNCHANGED <<nodeState, cancellationEpoch, implementationUsed,
                  validationCorrectionUsed, reviewUsed, repairUsed,
                  closureUsed, blockersFrozen, publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

Admit(n) ==
  /\ Running
  /\ nodeState[n] = "PENDING"
  /\ DependenciesDone(n)
  /\ nodeState' = [nodeState EXCEPT ![n] = "ADMITTED"]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

Implement(n) ==
  /\ Running
  /\ nodeState[n] = "ADMITTED"
  /\ ~implementationUsed[n]
  /\ nodeState' = [nodeState EXCEPT ![n] = "IMPLEMENTING"]
  /\ implementationUsed' = [implementationUsed EXCEPT ![n] = TRUE]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  validationCorrectionUsed, reviewUsed, repairUsed,
                  closureUsed, blockersFrozen, publicationRemaining>>
  /\ SpendBudget
  /\ KeepContract

ImplementationDone(n) ==
  /\ Running
  /\ nodeState[n] = "IMPLEMENTING"
  /\ nodeState' = [nodeState EXCEPT ![n] = "VALIDATING"]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

ValidationCorrection(n) ==
  /\ Running
  /\ nodeState[n] = "VALIDATING"
  /\ ~validationCorrectionUsed[n]
  /\ nodeState' = [nodeState EXCEPT ![n] = "IMPLEMENTING"]
  /\ validationCorrectionUsed' =
       [validationCorrectionUsed EXCEPT ![n] = TRUE]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, reviewUsed, repairUsed, closureUsed,
                  blockersFrozen, publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

ValidationPass(n) ==
  /\ Running
  /\ nodeState[n] = "VALIDATING"
  /\ nodeState' = [nodeState EXCEPT ![n] = "CANDIDATE_FROZEN"]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

Review(n) ==
  /\ Running
  /\ nodeState[n] = "CANDIDATE_FROZEN"
  /\ ~reviewUsed[n]
  /\ nodeState' = [nodeState EXCEPT ![n] = "CHECKS_AND_REVIEW"]
  /\ reviewUsed' = [reviewUsed EXCEPT ![n] = TRUE]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, repairUsed,
                  closureUsed, blockersFrozen, publicationRemaining>>
  /\ SpendBudget
  /\ KeepContract

FreezeFindings(n, hasBlockers) ==
  /\ Running
  /\ nodeState[n] = "CHECKS_AND_REVIEW"
  /\ hasBlockers \in BOOLEAN
  /\ nodeState' = [nodeState EXCEPT ![n] = "FINDINGS_FROZEN"]
  /\ blockersFrozen' = [blockersFrozen EXCEPT ![n] = hasBlockers]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

MarkReady(n) ==
  /\ Running
  /\ nodeState[n] = "FINDINGS_FROZEN"
  /\ ~blockersFrozen[n]
  /\ nodeState' = [nodeState EXCEPT ![n] = "READY_TO_PUBLISH"]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

AuthorizeRepair(n) ==
  /\ Running
  /\ nodeState[n] = "FINDINGS_FROZEN"
  /\ blockersFrozen[n]
  /\ ~repairUsed[n]
  /\ nodeState' = [nodeState EXCEPT ![n] = "REPAIR_AUTHORIZED"]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

Repair(n) ==
  /\ Running
  /\ nodeState[n] = "REPAIR_AUTHORIZED"
  /\ ~repairUsed[n]
  /\ nodeState' = [nodeState EXCEPT ![n] = "REPAIRING"]
  /\ repairUsed' = [repairUsed EXCEPT ![n] = TRUE]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  closureUsed, blockersFrozen, publicationRemaining>>
  /\ SpendBudget
  /\ KeepContract

RepairDone(n) ==
  /\ Running
  /\ nodeState[n] = "REPAIRING"
  /\ nodeState' = [nodeState EXCEPT ![n] = "REVALIDATING"]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

RevalidationPass(n) ==
  /\ Running
  /\ nodeState[n] = "REVALIDATING"
  /\ ~closureUsed[n]
  /\ nodeState' = [nodeState EXCEPT ![n] = "CLOSURE"]
  /\ closureUsed' = [closureUsed EXCEPT ![n] = TRUE]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, blockersFrozen, publicationRemaining>>
  /\ SpendBudget
  /\ KeepContract

ClosurePass(n) ==
  /\ Running
  /\ nodeState[n] = "CLOSURE"
  /\ nodeState' = [nodeState EXCEPT ![n] = "READY_TO_PUBLISH"]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

ClosureFail(n) ==
  /\ Running
  /\ nodeState[n] = "CLOSURE"
  /\ nodeState' = [nodeState EXCEPT ![n] = "FAILED_EXACT_NODE"]
  /\ campaign' = "FAILED"
  /\ revision' = revision + 1
  /\ UNCHANGED <<authorityEpoch, cancellationEpoch, implementationUsed,
                  validationCorrectionUsed, reviewUsed, repairUsed,
                  closureUsed, blockersFrozen, publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

StartPublish(n) ==
  /\ Running
  /\ nodeState[n] = "READY_TO_PUBLISH"
  /\ publicationRemaining[n] > 0
  /\ nodeState' = [nodeState EXCEPT ![n] = "PUBLISHING"]
  /\ revision' = revision + 1
  /\ UNCHANGED <<campaign, authorityEpoch, cancellationEpoch,
                  implementationUsed, validationCorrectionUsed, reviewUsed,
                  repairUsed, closureUsed, blockersFrozen,
                  publicationRemaining>>
  /\ SpendBudget
  /\ KeepContract

PublishConfirmed(n) ==
  /\ Running
  /\ nodeState[n] = "PUBLISHING"
  /\ publicationRemaining[n] > 0
  /\ publicationRemaining' =
       [publicationRemaining EXCEPT ![n] = @ - 1]
  /\ nodeState' = [nodeState EXCEPT
       ![n] = IF publicationRemaining[n] = 1
               THEN "DONE" ELSE "READY_TO_PUBLISH"]
  /\ campaign' =
       IF publicationRemaining[n] = 1
          /\ \A m \in Nodes \ {n} : nodeState[m] = "DONE"
       THEN "COMPLETED" ELSE "RUNNING"
  /\ revision' = revision + 1
  /\ UNCHANGED <<authorityEpoch, cancellationEpoch, implementationUsed,
                  validationCorrectionUsed, reviewUsed, repairUsed,
                  closureUsed, blockersFrozen>>
  /\ KeepBudget
  /\ KeepContract

FailNode(n) ==
  /\ campaign \notin TerminalCampaignStates
  /\ nodeState[n] \notin TerminalNodeStates
  /\ nodeState' = [nodeState EXCEPT ![n] = "FAILED_EXACT_NODE"]
  /\ campaign' = "FAILED"
  /\ revision' = revision + 1
  /\ UNCHANGED <<authorityEpoch, cancellationEpoch, implementationUsed,
                  validationCorrectionUsed, reviewUsed, repairUsed,
                  closureUsed, blockersFrozen, publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

Cancel ==
  /\ campaign \notin TerminalCampaignStates
  /\ campaign' = "CANCELLED"
  /\ cancellationEpoch' = cancellationEpoch + 1
  /\ nodeState' = [n \in Nodes |->
       IF nodeState[n] \in TerminalNodeStates THEN nodeState[n]
       ELSE "CANCELLED"]
  /\ revision' = revision + 1
  /\ UNCHANGED <<authorityEpoch, implementationUsed,
                  validationCorrectionUsed, reviewUsed, repairUsed,
                  closureUsed, blockersFrozen, publicationRemaining>>
  /\ KeepBudget
  /\ KeepContract

AutonomousNodeAction ==
  \E n \in Nodes :
    Admit(n) \/ Implement(n) \/ ImplementationDone(n) \/
    ValidationCorrection(n) \/ ValidationPass(n) \/ Review(n) \/
    (\E b \in BOOLEAN : FreezeFindings(n, b)) \/ MarkReady(n) \/
    AuthorizeRepair(n) \/ Repair(n) \/ RepairDone(n) \/
    RevalidationPass(n) \/ ClosurePass(n) \/ ClosureFail(n) \/
    StartPublish(n) \/ PublishConfirmed(n)

Next ==
  /\ revision < MaxRevision
  /\ \/ Approve
     \/ Start
     \/ WaitExternal
     \/ WaitHuman
     \/ Resume
     \/ AdvanceAuthority
     \/ AutonomousNodeAction
     \/ \E n \in Nodes : FailNode(n)
     \/ Cancel

TypeInvariant ==
  /\ campaign \in CampaignStates
  /\ nodeState \in [Nodes -> NodeStates]
  /\ revision \in 0..MaxRevision
  /\ authorityEpoch \in Nat
  /\ cancellationEpoch \in Nat
  /\ rankRemaining \in 0..InitialBudget
  /\ budgetReceipts \in 0..InitialBudget
  /\ implementationUsed \in [Nodes -> BOOLEAN]
  /\ validationCorrectionUsed \in [Nodes -> BOOLEAN]
  /\ reviewUsed \in [Nodes -> BOOLEAN]
  /\ repairUsed \in [Nodes -> BOOLEAN]
  /\ closureUsed \in [Nodes -> BOOLEAN]
  /\ blockersFrozen \in [Nodes -> BOOLEAN]
  /\ publicationRemaining \in [Nodes -> 0..RequiredPublicationEffects]

GraphIsFiniteDAG ==
  /\ IsFiniteSet(Nodes)
  /\ \A n \in Nodes :
       /\ Dependencies[n] \subseteq Nodes
       /\ \A d \in Dependencies[n] : TopoRank[d] < TopoRank[n]

GraphImmutable == graphDigest = InitialGraphDigest

ContractImmutable ==
  /\ contractCampaignId = CampaignId
  /\ contractSpecRevision = SpecificationRevision
  /\ contractSpecDigest = SpecificationDigest
  /\ graphDigest = InitialGraphDigest

NoSuccessorGeneration == campaignGeneration = 1

BudgetConservation == rankRemaining + budgetReceipts = InitialBudget

LifecycleLimits ==
  \A n \in Nodes :
    /\ repairUsed[n] => reviewUsed[n]
    /\ closureUsed[n] => repairUsed[n]
    /\ nodeState[n] \in {
         "CHECKS_AND_REVIEW", "FINDINGS_FROZEN", "REPAIR_AUTHORIZED",
         "REPAIRING", "REVALIDATING", "CLOSURE", "READY_TO_PUBLISH",
         "PUBLISHING", "DONE"
       } => reviewUsed[n]
    /\ nodeState[n] \in {
         "REPAIRING", "REVALIDATING", "CLOSURE"
       } => repairUsed[n]
    /\ nodeState[n] = "CLOSURE" => closureUsed[n]

CampaignTerminalShape ==
  /\ (campaign = "COMPLETED" =>
       \A n \in Nodes : nodeState[n] = "DONE")
  /\ (campaign = "FAILED" =>
       \E n \in Nodes : nodeState[n] = "FAILED_EXACT_NODE")
  /\ (campaign = "CANCELLED" =>
       \A n \in Nodes : nodeState[n] \in TerminalNodeStates)

CancelledIsTerminal == campaign = "CANCELLED" => ~ENABLED Next
TerminalCampaignIsTerminal == campaign \in TerminalCampaignStates => ~ENABLED Next
WaitingYields ==
  campaign \in WaitingCampaignStates => ~ENABLED AutonomousNodeAction

RankStep == rankRemaining' <= rankRemaining
RevisionStep == revision' = revision + 1
BudgetStep ==
  \/ /\ rankRemaining' = rankRemaining
     /\ budgetReceipts' = budgetReceipts
  \/ /\ rankRemaining' = rankRemaining - 1
     /\ budgetReceipts' = budgetReceipts + 1
UsageStep ==
  \A n \in Nodes :
    /\ (implementationUsed[n] => implementationUsed'[n])
    /\ (validationCorrectionUsed[n] => validationCorrectionUsed'[n])
    /\ (reviewUsed[n] => reviewUsed'[n])
    /\ (repairUsed[n] => repairUsed'[n])
    /\ (closureUsed[n] => closureUsed'[n])
ContractStep == contract' = contract
WaitingBudgetStep ==
  campaign \in WaitingCampaignStates => rankRemaining' = rankRemaining

RankNeverIncreases == [] [RankStep]_vars
RevisionAlwaysAdvances == [] [RevisionStep]_vars
AutonomousBudgetIsOneWay == [] [BudgetStep]_vars
LifecycleUseIsOneWay == [] [UsageStep]_vars
ApprovedContractNeverChanges == [] [ContractStep]_vars
WaitingConsumesNoAutonomousBudget == [] [WaitingBudgetStep]_vars

Spec == Init /\ [][Next]_vars

=============================================================================
