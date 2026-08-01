import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import "./design/base.css";
import { Shell } from "./components/Shell";
import { EpisodePage } from "./routes/Episode";
import { HomePage } from "./routes/Home";
import { JudgePage } from "./routes/Judge";
import { ShowPage } from "./routes/Show";

const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      { path: "/", element: <HomePage /> },
      { path: "/show/:showId", element: <ShowPage /> },
      { path: "/ep/:epId", element: <EpisodePage /> },
      { path: "/judge", element: <JudgePage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
