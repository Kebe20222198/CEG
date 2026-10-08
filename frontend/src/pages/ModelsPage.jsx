import React, { useEffect, useState } from 'react';
import { Cpu, Server, Brain } from 'lucide-react';
import { apiGet } from '../api';

// What CEG chooses from and runs on: the model registry, the execution
// backends, and what the learned optimiser has observed so far.

const pct = (v) => (v == null ? '—' : `${Math.round(v * 100)} %`);

export default function ModelsPage() {
  const [models, setModels] = useState([]);
  const [backends, setBackends] = useState([]);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    apiGet('/models').then(setModels).catch(() => setModels([]));
    apiGet('/backends').then(setBackends).catch(() => setBackends([]));
    apiGet('/optimizer/statistics').then(setStats).catch(() => setStats(null));
  }, []);

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Modèles & optimiseur</h1>
          <p className="page-subtitle">
            Les modèles parmi lesquels le Runtime Decision Engine choisit pour chaque étape, les
            moteurs capables d'exécuter un plan, et ce que l'optimiseur appris a observé.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><Cpu size={14} /> Registre des modèles (simulés)</div>
        <table className="table">
          <thead>
            <tr>
              <th>Modèle</th>
              <th>Tier</th>
              <th>Coût / appel</th>
              <th>Latence</th>
              <th>Capacités</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.id}>
                <td className="mono nowrap">{m.id}</td>
                <td><span className="chip">{m.tier}</span></td>
                <td className="mono">${m.estimated_cost.toFixed(3)}</td>
                <td className="mono">{m.estimated_latency_ms} ms</td>
                <td className="small muted">{m.supported_capabilities.join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-header"><Server size={14} /> Backends d'exécution</div>
        <table className="table">
          <thead>
            <tr>
              <th>Backend</th>
              <th>Approbation humaine</th>
            </tr>
          </thead>
          <tbody>
            {backends.map((b) => (
              <tr key={b.id}>
                <td className="mono">{b.id}</td>
                <td>{b.supports_hitl ? 'oui' : 'non — un plan qui en demande est refusé'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-header"><Brain size={14} /> Optimiseur appris</div>
        {!stats || stats.observations.length === 0 ? (
          <div className="empty">
            Aucune observation : lancez une exécution avec l'optimiseur « learned ».
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Modèle</th>
                <th>Capacité</th>
                <th>Appels observés</th>
                <th>Taux de succès</th>
                <th>Qualité moyenne</th>
              </tr>
            </thead>
            <tbody>
              {stats.observations.map((o) => (
                <tr key={`${o.model}-${o.capability}`}>
                  <td className="mono">{o.model}</td>
                  <td className="mono small">{o.capability}</td>
                  <td className="mono">{o.n}</td>
                  <td className="mono">{pct(o.success_rate)}</td>
                  <td className="mono">{o.mean_quality ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {stats && (
          <div className="card-body small muted">
            Poids de l'a priori : {stats.prior_weight} observations · exploration : {stats.exploration}
          </div>
        )}
      </div>
    </>
  );
}
