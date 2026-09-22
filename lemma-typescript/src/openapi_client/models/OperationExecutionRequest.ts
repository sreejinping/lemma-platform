/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OperationExecutionRequest = {
    account_id?: (string | null);
    act_as?: OperationExecutionRequest.act_as;
    payload: Record<string, any>;
};
export namespace OperationExecutionRequest {
    export enum act_as {
        USER = 'user',
        APP = 'app',
    }
}
