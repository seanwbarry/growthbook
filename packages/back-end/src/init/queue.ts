import addExperimentResultsJob from "../jobs/updateExperimentResults";
import refreshFactTableColumns from "../jobs/refreshFactTableColumns";
import updateScheduledFeatures from "../jobs/updateScheduledFeatures";
import addWebhooksJob from "../jobs/webhooks";
import addMetricUpdateJob from "../jobs/updateMetrics";
import addProxyUpdateJob from "../jobs/proxyUpdate";
import createInformationSchemaJob from "../jobs/createInformationSchema";
import updateInformationSchemaJob from "../jobs/updateInformationSchema";
import createAutoGeneratedMetrics from "../jobs/createAutoGeneratedMetrics";
import { CRON_ENABLED, IS_CLOUD } from "../util/secrets";
import { getAgendaInstance } from "../services/queueing";
import updateStaleInformationSchemaTable from "../jobs/updateStaleInformationSchemaTable";
import expireOldQueries from "../jobs/expireOldQueries";
import addSdkWebhooksJob from "../jobs/sdkWebhooks";
import updateLicenseJob, { queueUpdateLicense } from "../jobs/updateLicense";

export async function queueInit() {
  if (!CRON_ENABLED) return;
  const agenda = getAgendaInstance();

  addExperimentResultsJob(agenda);
  updateScheduledFeatures(agenda);
  addMetricUpdateJob(agenda);
  addWebhooksJob(agenda);
  addProxyUpdateJob(agenda);
  createInformationSchemaJob(agenda);
  updateInformationSchemaJob(agenda);
  createAutoGeneratedMetrics(agenda);
  updateStaleInformationSchemaTable(agenda);
  expireOldQueries(agenda);
  refreshFactTableColumns(agenda);
  addSdkWebhooksJob(agenda);
  updateLicenseJob(agenda);

  await agenda.start();

  if (!IS_CLOUD) {
    await queueUpdateLicense();
  }
}
