/**
 * The slice of ECharts that the dashboards use, registered once. This file is only ever imported dynamically, so the library (the
 * largest dependency of the console) is a chunk of its own that loads when the first chart is drawn, not when someone signs in.
 */
import { BarChart, HeatmapChart, LineChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";

echarts.use([BarChart, LineChart, HeatmapChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, VisualMapComponent, AriaComponent, SVGRenderer]);

export { echarts };
export type { EChartsOption } from "echarts";
