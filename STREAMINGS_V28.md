# Linkflix V28 — Streamings

- O menu **Em Breve** virou **Streamings**.
- `/streamings`: hub geral com banner, plataformas e prateleiras.
- `/streaming/netflix`, `/streaming/disney`, `/streaming/max`, `/streaming/prime`, `/streaming/marvel`, `/streaming/dc`, `/streaming/bond`, `/streaming/starwars`: vitrines individuais.
- `/admin/streamings`: painel separado para cadastrar conteúdos por plataforma.
- O Admin Streamings aceita importação do TMDB e reaproveita conteúdos existentes quando encontra o mesmo TMDB ID/título, evitando duplicar o catálogo.
- Se marcar **banner principal**, o título vira o destaque daquela plataforma.
- `Top 10 Hoje` aceita posição de 1 a 10.
- Os campos novos são criados automaticamente no PostgreSQL sem apagar os dados existentes.
