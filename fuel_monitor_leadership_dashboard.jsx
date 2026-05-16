import React, { useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { AlertTriangle, Car, Database, Droplets, RefreshCcw, ShieldAlert, TrendingUp } from "lucide-react";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid } from "recharts";

type SourceStatus = {
  name: string;
  status: "ok" | "warning" | "error";
  rowsLoaded: number;
  lastSync: string;
  detail: string;
};

type VehicleRow = {
  plate: string;
  user: string;
  directorate: string;
  liters: number;
  limit: number;
  remaining: number;
  usagePct: number;
  status: "OK" | "WARNING" | "CRITICAL" | "EXCEEDED";
  lastFueling: string;
  source: string;
};

type AlertRow = {
  plate: string;
  user: string;
  threshold: string;
  usagePct: number;
  remaining: number;
  updatedAt: string;
  status: VehicleRow["status"];
};

const sourceStatuses: SourceStatus[] = [
  { name: "Turpak", status: "ok", rowsLoaded: 1374, lastSync: "Сегодня 09:10", detail: "Период: с 1 числа месяца" },
  { name: "Petrol Ofisi", status: "ok", rowsLoaded: 418, lastSync: "Сегодня 09:08", detail: "Загрузка чанками по 1 дню" },
  { name: "Shell Excel", status: "warning", rowsLoaded: 83, lastSync: "Сегодня 08:55", detail: "Последняя выгрузка: 14.04.2026" },
  { name: "Driver Roster", status: "ok", rowsLoaded: 214, lastSync: "Сегодня 08:57", detail: "С учётом листа Подменные Yedekler" },
];

const vehicles: VehicleRow[] = [
  { plate: "06EMY474", user: "Ahmet Demir", directorate: "Transport", liters: 286, limit: 300, remaining: 14, usagePct: 95.3, status: "CRITICAL", lastFueling: "Сегодня 07:42", source: "Petrol" },
  { plate: "33EA665", user: "Mehmet Kaya", directorate: "Logistics", liters: 244, limit: 300, remaining: 56, usagePct: 81.3, status: "WARNING", lastFueling: "Сегодня 08:06", source: "Turpak" },
  { plate: "01AIF862", user: "Serkan Yılmaz", directorate: "Admin", liters: 301, limit: 300, remaining: -1, usagePct: 100.3, status: "EXCEEDED", lastFueling: "Вчера 19:11", source: "Shell" },
  { plate: "34HTK378", user: "Yedek Araç", directorate: "Reserve", liters: 122, limit: 300, remaining: 178, usagePct: 40.7, status: "OK", lastFueling: "Вчера 16:50", source: "Turpak" },
  { plate: "33DT645", user: "İsmail Çetin", directorate: "Operations", liters: 271, limit: 300, remaining: 29, usagePct: 90.3, status: "CRITICAL", lastFueling: "Сегодня 06:58", source: "Petrol" },
  { plate: "06EMY594", user: "Mustafa Arslan", directorate: "Operations", liters: 199, limit: 300, remaining: 101, usagePct: 66.3, status: "OK", lastFueling: "Сегодня 05:47", source: "Turpak" },
];

const alerts: AlertRow[] = [
  { plate: "01AIF862", user: "Serkan Yılmaz", threshold: "100%", usagePct: 100.3, remaining: -1, updatedAt: "Сегодня 09:10", status: "EXCEEDED" },
  { plate: "06EMY474", user: "Ahmet Demir", threshold: "90%", usagePct: 95.3, remaining: 14, updatedAt: "Сегодня 09:10", status: "CRITICAL" },
  { plate: "33DT645", user: "İsmail Çetin", threshold: "90%", usagePct: 90.3, remaining: 29, updatedAt: "Сегодня 09:10", status: "CRITICAL" },
  { plate: "33EA665", user: "Mehmet Kaya", threshold: "80%", usagePct: 81.3, remaining: 56, updatedAt: "Сегодня 09:10", status: "WARNING" },
];

const topConsumption = [
  { plate: "01AIF862", liters: 301 },
  { plate: "06EMY474", liters: 286 },
  { plate: "33DT645", liters: 271 },
  { plate: "33EA665", liters: 244 },
  { plate: "06EMY594", liters: 199 },
];

function statusBadge(status: string) {
  if (status === "EXCEEDED") return "bg-red-600 text-white";
  if (status === "CRITICAL") return "bg-orange-500 text-white";
  if (status === "WARNING") return "bg-yellow-400 text-black";
  return "bg-emerald-600 text-white";
}

function sourceBadge(status: SourceStatus["status"]) {
  if (status === "error") return "bg-red-600 text-white";
  if (status === "warning") return "bg-yellow-400 text-black";
  return "bg-emerald-600 text-white";
}

export default function FuelMonitorLeadershipDashboard() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const filteredVehicles = useMemo(() => {
    return vehicles.filter((v) => {
      const q = search.trim().toLowerCase();
      const bySearch = !q || [v.plate, v.user, v.directorate].join(" ").toLowerCase().includes(q);
      const byStatus = statusFilter === "all" || v.status === statusFilter;
      return bySearch && byStatus;
    });
  }, [search, statusFilter]);

  const totalLiters = vehicles.reduce((sum, v) => sum + v.liters, 0);
  const totalLimit = vehicles.reduce((sum, v) => sum + v.limit, 0);
  const nearLimitCount = vehicles.filter((v) => ["WARNING", "CRITICAL", "EXCEEDED"].includes(v.status)).length;
  const exceededCount = vehicles.filter((v) => v.status === "EXCEEDED").length;
  const criticalCount = vehicles.filter((v) => v.status === "CRITICAL").length;
  const avgUsage = totalLimit ? Math.round((totalLiters / totalLimit) * 100) : 0;

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="flex flex-col gap-4 rounded-3xl bg-white p-6 shadow-sm md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-sm text-slate-500">Оперативная витрина для руководства</div>
            <h1 className="text-3xl font-semibold tracking-tight">Топливный мониторинг</h1>
            <div className="mt-2 text-sm text-slate-600">
              Текущий месяц · Последнее обновление: сегодня 09:10 · Данные: Turpak / Petrol / Shell / Driver roster
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" className="rounded-2xl">
              <RefreshCcw className="mr-2 h-4 w-4" /> Обновить
            </Button>
            <Button className="rounded-2xl">Скачать отчёт</Button>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
          <Card className="rounded-3xl shadow-sm">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium text-slate-600">
                <Droplets className="h-4 w-4" /> Заправлено за месяц
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-semibold">{totalLiters.toFixed(0)} л</div>
              <div className="mt-1 text-sm text-slate-500">По всем источникам</div>
            </CardContent>
          </Card>

          <Card className="rounded-3xl shadow-sm">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium text-slate-600">
                <TrendingUp className="h-4 w-4" /> Средняя утилизация
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-semibold">{avgUsage}%</div>
              <div className="mt-3"><Progress value={avgUsage} /></div>
            </CardContent>
          </Card>

          <Card className="rounded-3xl shadow-sm">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium text-slate-600">
                <AlertTriangle className="h-4 w-4" /> Близко к лимиту
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-semibold">{nearLimitCount}</div>
              <div className="mt-1 text-sm text-slate-500">WARNING / CRITICAL / EXCEEDED</div>
            </CardContent>
          </Card>

          <Card className="rounded-3xl shadow-sm">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium text-slate-600">
                <ShieldAlert className="h-4 w-4" /> Критические
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-semibold">{criticalCount}</div>
              <div className="mt-1 text-sm text-slate-500">На уровне 90%+</div>
            </CardContent>
          </Card>

          <Card className="rounded-3xl shadow-sm">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium text-slate-600">
                <Car className="h-4 w-4" /> Превышение
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-semibold">{exceededCount}</div>
              <div className="mt-1 text-sm text-slate-500">Машины выше 100%</div>
            </CardContent>
          </Card>
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <Card className="rounded-3xl shadow-sm xl:col-span-2">
            <CardHeader>
              <CardTitle>Топ машин по расходу</CardTitle>
            </CardHeader>
            <CardContent className="h-[320px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={topConsumption}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="plate" />
                  <YAxis />
                  <Tooltip />
                  <Bar dataKey="liters" radius={[8, 8, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card className="rounded-3xl shadow-sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database className="h-5 w-5" /> Статус источников
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {sourceStatuses.map((s) => (
                <div key={s.name} className="rounded-2xl border p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="font-medium">{s.name}</div>
                    <Badge className={sourceBadge(s.status)}>{s.status.toUpperCase()}</Badge>
                  </div>
                  <div className="mt-2 text-sm text-slate-600">Строк загружено: {s.rowsLoaded}</div>
                  <div className="text-sm text-slate-600">Последний sync: {s.lastSync}</div>
                  <div className="mt-1 text-xs text-slate-500">{s.detail}</div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <Card className="rounded-3xl shadow-sm xl:col-span-2">
            <CardHeader>
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <CardTitle>Реестр машин</CardTitle>
                <div className="flex flex-col gap-2 md:flex-row">
                  <Input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Поиск по номеру, пользователю, дирекции"
                    className="w-full rounded-2xl md:w-[320px]"
                  />
                  <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="w-full rounded-2xl md:w-[180px]">
                      <SelectValue placeholder="Статус" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Все статусы</SelectItem>
                      <SelectItem value="OK">OK</SelectItem>
                      <SelectItem value="WARNING">WARNING</SelectItem>
                      <SelectItem value="CRITICAL">CRITICAL</SelectItem>
                      <SelectItem value="EXCEEDED">EXCEEDED</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-slate-500">
                      <th className="pb-3 pr-4">Госномер</th>
                      <th className="pb-3 pr-4">Пользователь</th>
                      <th className="pb-3 pr-4">Дирекция</th>
                      <th className="pb-3 pr-4">Литры</th>
                      <th className="pb-3 pr-4">Лимит</th>
                      <th className="pb-3 pr-4">Остаток</th>
                      <th className="pb-3 pr-4">Утилизация</th>
                      <th className="pb-3 pr-4">Статус</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredVehicles.map((v) => (
                      <tr key={v.plate} className="border-b last:border-0">
                        <td className="py-3 pr-4 font-medium">{v.plate}</td>
                        <td className="py-3 pr-4">{v.user}</td>
                        <td className="py-3 pr-4">{v.directorate}</td>
                        <td className="py-3 pr-4">{v.liters.toFixed(0)}</td>
                        <td className="py-3 pr-4">{v.limit.toFixed(0)}</td>
                        <td className="py-3 pr-4">{v.remaining.toFixed(0)}</td>
                        <td className="py-3 pr-4 w-[180px]">
                          <div className="space-y-1">
                            <div className="flex items-center justify-between text-xs text-slate-500">
                              <span>{v.usagePct.toFixed(1)}%</span>
                              <span>{v.lastFueling}</span>
                            </div>
                            <Progress value={Math.min(v.usagePct, 100)} />
                          </div>
                        </td>
                        <td className="py-3 pr-4">
                          <Badge className={statusBadge(v.status)}>{v.status}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <Card className="rounded-3xl shadow-sm">
            <CardHeader>
              <CardTitle>Активные алерты</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {alerts.map((a) => (
                <div key={`${a.plate}-${a.threshold}`} className="rounded-2xl border p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="font-medium">{a.plate}</div>
                      <div className="text-sm text-slate-500">{a.user}</div>
                    </div>
                    <Badge className={statusBadge(a.status)}>{a.threshold}</Badge>
                  </div>
                  <div className="mt-3 space-y-1 text-sm text-slate-600">
                    <div>Утилизация: {a.usagePct.toFixed(1)}%</div>
                    <div>Остаток: {a.remaining.toFixed(0)} л</div>
                    <div>Обновлено: {a.updatedAt}</div>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
