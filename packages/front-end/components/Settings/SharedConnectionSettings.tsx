import { DataSourceSettings } from "@back-end/types/datasource";
import { ChangeEventHandler } from "react";

export interface Props {
  settings: Partial<DataSourceSettings>;
  onSettingChange: ChangeEventHandler<HTMLInputElement | HTMLSelectElement>;
}

export default function SharedConnectionSettings({
  settings,
  onSettingChange,
}: Props) {
  return (
    <>
      <div className="row">
        <div className="form-group col-md-12">
          <label>Maximum Concurrent Connections</label>
          <input
            type="number"
            className="form-control"
            name="maxConcurrentQueries"
            value={settings.maxConcurrentQueries || ""}
            onChange={onSettingChange}
          />
        </div>
      </div>
    </>
  );
}
