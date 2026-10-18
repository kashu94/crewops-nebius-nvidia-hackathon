// Channel namespace handlers for the CrewOps live console (AppSync Events, APPSYNC_JS).
//
// Only staff who may read disruptions can subscribe: controller, approver, auditor, admin
// and super admin, as Cognito groups. Crew and reporters cannot. Publishing is IAM-only
// (the notifier function), set on the API, so no publish handler is needed.
import { util } from '@aws-appsync/utils'

const READERS = ['controller', 'approver', 'auditor', 'admin', 'super_admin']

export function onSubscribe(ctx) {
  const groups = (ctx.identity && ctx.identity.groups) || []
  if (!groups.some((group) => READERS.includes(group))) {
    util.unauthorized()
  }
}
