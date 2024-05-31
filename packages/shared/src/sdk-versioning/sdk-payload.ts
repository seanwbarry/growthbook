import {
  AutoExperimentWithProject,
  FeatureDefinitionWithProject,
} from "back-end/types/api";
import { pick, omit } from "lodash";
import cloneDeep from "lodash/cloneDeep";
import { SavedGroupsValues } from "../types";
import { SDKCapability } from "./index";

const strictFeatureKeys = ["defaultValue", "rules"];
const strictFeatureRuleKeys = [
  "key",
  "variations",
  "weights",
  "coverage",
  "condition",
  "namespace",
  "force",
  "hashAttribute",
];
const bucketingV2Keys = [
  "hashVersion",
  "range",
  "ranges",
  "meta",
  "filters",
  "seed",
  "name",
  "phase",
];
const stickyBucketingKeys = [
  "fallbackAttribute",
  "disableStickyBucketing",
  "bucketVersion",
  "minBucketVersion",
];
const prerequisiteKeys = ["parentConditions"];

// eslint-disable-next-line
type Node = [string, any];
// eslint-disable-next-line
export type NodeHandler = (node: Node, object: any) => void;

// Recursively traverses the given object and calls onNode on each key/value pair.
// If onNode modifies the object in place, it walks the new values as they're inserted, updated, or deleted
// eslint-disable-next-line
export const recursiveWalk = (object: any, onNode: NodeHandler) => {
  // Base case: stop recursion once you hit a primitive or null
  if (object === null || typeof object !== "object") {
    return;
  }
  // If currently walking over an object or array, iterate the entries and call onNode before recurring
  Object.entries(object).forEach((node) => {
    onNode(node, object);
    // Recompute the reference for the recursive call as the key may have changed
    recursiveWalk(object[node[0]], onNode);
  });
};

export const scrubFeatures = (
  features: Record<string, FeatureDefinitionWithProject>,
  capabilities: SDKCapability[],
  savedGroups: SavedGroupsValues
): Record<string, FeatureDefinitionWithProject> => {
  const allowedFeatureKeys = [...strictFeatureKeys];
  const allowedFeatureRuleKeys = [...strictFeatureRuleKeys];
  if (capabilities.includes("bucketingV2")) {
    allowedFeatureRuleKeys.push(...bucketingV2Keys);
  }
  if (capabilities.includes("stickyBucketing")) {
    allowedFeatureRuleKeys.push(...stickyBucketingKeys);
  }
  if (capabilities.includes("prerequisites")) {
    allowedFeatureRuleKeys.push(...prerequisiteKeys);
  }
  if (!capabilities.includes("savedGroupReferences")) {
    Object.values(features).forEach((feature) => {
      if (!feature.rules) {
        return;
      }
      feature.rules.forEach((rule) => {
        recursiveWalk(rule.condition, replaceSavedGroups(savedGroups));
        recursiveWalk(rule.parentConditions, replaceSavedGroups(savedGroups));
      });
    });
  }

  const newFeatures = cloneDeep(features);

  // Remove features that have any gating parentConditions & any rules that have parentConditions
  // Note: Reduction of features and rules is already performed in the back-end
  //   see: reduceFeaturesWithPrerequisites()
  if (!capabilities.includes("prerequisites")) {
    for (const k in newFeatures) {
      // delete feature
      if (
        newFeatures[k]?.rules?.some((rule) =>
          rule?.parentConditions?.some((pc) => !!pc.gate)
        )
      ) {
        delete newFeatures[k];
        continue;
      }
      // delete rules
      newFeatures[k].rules = newFeatures[k].rules?.filter(
        (rule) => (rule.parentConditions?.length ?? 0) === 0
      );
    }
  }

  if (capabilities.includes("looseUnmarshalling")) {
    return newFeatures;
  }

  for (const k in newFeatures) {
    newFeatures[k] = pick(
      newFeatures[k],
      allowedFeatureKeys
    ) as FeatureDefinitionWithProject;
    if (newFeatures[k]?.rules) {
      newFeatures[k].rules = newFeatures[k].rules?.map((rule) => {
        rule = {
          ...pick(rule, allowedFeatureRuleKeys),
        };
        return rule;
      });
    }
  }

  return newFeatures;
};

export const scrubExperiments = (
  experiments: AutoExperimentWithProject[],
  capabilities: SDKCapability[],
  savedGroups: SavedGroupsValues
): AutoExperimentWithProject[] => {
  const removedExperimentKeys: string[] = [];
  const supportsPrerequisites = capabilities.includes("prerequisites");
  const supportsRedirects = capabilities.includes("redirects");

  if (!capabilities.includes("savedGroupReferences")) {
    experiments.forEach((experimentDefinition) => {
      recursiveWalk(
        experimentDefinition.condition,
        replaceSavedGroups(savedGroups)
      );
      recursiveWalk(
        experimentDefinition.parentConditions,
        replaceSavedGroups(savedGroups)
      );
    });
  }

  if (supportsPrerequisites && supportsRedirects) return experiments;

  if (!supportsPrerequisites) {
    removedExperimentKeys.push(...prerequisiteKeys);
  }

  const newExperiments: AutoExperimentWithProject[] = [];

  for (let experiment of experiments) {
    // Filter out any url redirect auto experiments if not supported
    if (!supportsRedirects && experiment.changeType === "redirect") {
      continue;
    }

    // Filter out experiments that have any parentConditions
    if (
      !supportsPrerequisites &&
      (experiment.parentConditions?.length ?? 0) > 0
    ) {
      continue;
    }

    // Scrub fields from the experiment
    experiment = omit(
      experiment,
      removedExperimentKeys
    ) as AutoExperimentWithProject;

    newExperiments.push(experiment);
  }
  return newExperiments;
};

export const scrubSavedGroups = (
  savedGroups: SavedGroupsValues,
  capabilities: SDKCapability[]
): SavedGroupsValues | undefined => {
  if (!capabilities.includes("savedGroupReferences")) {
    return undefined;
  }
  return savedGroups;
};

// Returns a handler which modifies the object in place, replacing saved group IDs with the contents of those groups
const replaceSavedGroups: (savedGroups: SavedGroupsValues) => NodeHandler = (
  savedGroups: SavedGroupsValues
) => {
  return ([key, value], object) => {
    if (key === "$inGroup" || key === "$notInGroup") {
      object[key.replace("Group", "")] = savedGroups[value] || [];
      delete object[key];
    }
  };
};
